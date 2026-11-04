"""Hermetic tests for read-only held legacy workload triage."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bridge
import legacy_hold_audit as audit
import repeat_v064


class LegacyAuditTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.state=self.root/"bridge-state"
        self.reports=self.root/"reports"
        for name in ("active","queue","done","diagnostics","completion-acks"):
            (self.state/name).mkdir(parents=True)
        self.reports.mkdir()
        for obj,name,value in [
            (audit,"STATE",self.state),(audit,"REPORTS",self.reports),
            (bridge,"ACTIVE",self.state/"active"),
            (bridge,"DIAG",self.state/"diagnostics")]:
            p=patch.object(obj,name,value)
            p.start()
            self.addCleanup(p.stop)
        self.id1="01234567-89ab-4cde-8901-000000008001"
        self.id2="01234567-89ab-4cde-8901-000000008002"
        (self.state/"admission-hold.json").write_text("{}",encoding="utf-8")

    def active(self,jid=None,doc=None,events=None):
        jid=jid or self.id1
        data=doc or {"id":jid,"completion_mode":"report",
                     "report_name":"completion-"+jid+".md",
                     "prompt":"SECRET PROMPT: never disclose"}
        (self.state/"active"/(jid+".json")).write_text(
            json.dumps(data),encoding="utf-8")
        if events is not None:
            (self.state/"diagnostics"/(jid+".json")).write_text(
                json.dumps([{"at":"2026-11-03T00:00:00+00:00",**e}
                            for e in events]),encoding="utf-8")
        return jid

    def accepted_report(self,jid,valid=True):
        content=("Job ID: "+jid+"\n## Outcome\nTest\n## Evidence\nOK").encode()
        (self.reports/("completion-"+jid+".md")).write_bytes(content)
        (self.state/"completion-acks"/(jid+".json")).write_text(json.dumps({
            "job_id":jid,"status":"REPORT_ACKED",
            "artifact_sha256":hashlib.sha256(content).hexdigest()
                               if valid else "0"*64
        }),encoding="utf-8")

    def test_recovers_only_with_server_evidence_and_matching_ack(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"}])
        self.accepted_report(jid)
        d=audit.snapshot()
        self.assertEqual(d["counts"]["total"],1)
        self.assertTrue(d["hold_present"])
        self.assertEqual(d["counts"]["acks_matching"],1)
        self.assertEqual(d["counts"]["recovery_eligible"],1)
        self.assertEqual(d["decisions"],{"await_owned_tab":1})
        self.assertNotIn("SECRET PROMPT",json.dumps(d))

    def test_unexpected_close_overrides_ack_and_recovery(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"},
            {"stage":"tab_closed","reason":"unexpected_close"}])
        self.accepted_report(jid)
        d=audit.snapshot()
        self.assertEqual(d["decisions"],{"manual_unexpected_close":1})
        self.assertEqual(d["counts"]["unexpected_close"],1)
        self.assertEqual(d["counts"]["recovery_eligible"],0)

    def test_invalid_ack_and_ambiguous_result_fail_closed(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"}])
        self.accepted_report(jid,valid=False)
        self.assertEqual(audit.snapshot()["decisions"],{"manual_disputed_ack":1})
        (self.state/"diagnostics"/(jid+".json")).write_text(json.dumps([
            {"stage":"unknown"}]),encoding="utf-8")
        self.assertEqual(audit.snapshot()["decisions"],{"manual_uncertain_result":1})

    def test_queue_and_state_conflict_cannot_be_declared_done(self):
        jid=self.id1
        (self.state/"queue"/(jid+".json")).write_text(
            json.dumps({"id":jid,"prompt":"SECRET"}),encoding="utf-8")
        self.assertEqual(audit.snapshot()["decisions"],{"held_no_dispatch":1})
        self.active(jid,events=[])
        d=audit.snapshot()
        self.assertEqual(d["counts"]["state_conflicts"],1)
        self.assertEqual(d["decisions"],{"manual_state_conflict":1})

    def test_unreadable_job_and_unknown_pre_send(self):
        jid=self.active()
        (self.state/"active"/(jid+".json")).write_text("[]",encoding="utf-8")
        self.assertEqual(audit.snapshot()["decisions"],{"manual_unreadable_job":1})
        self.active(jid)
        self.assertEqual(audit.snapshot()["decisions"],{"manual_presend_unknown":1})

    def test_fresh_zero_identified_browser_scan_forces_manual_review(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"}])
        self.accepted_report(jid)
        self.assertEqual(audit.snapshot()["decisions"],{"await_owned_tab":1})
        (self.state/"diagnostics"/"recovery-scan.json").write_text(json.dumps({
            "at":datetime.now(timezone.utc).isoformat(),
            "identified":0,"eligible":0,"recovered":0,
            "tabs_total":10,"chat_tabs":0
        }),encoding="utf-8")
        d=audit.snapshot()
        self.assertEqual(d["decisions"],{"manual_browser_tab_not_found":1})
        self.assertTrue(d["fresh_scan_zero_agents"])
        self.assertEqual(d["counts"]["acks_matching"],1)

    def test_stale_or_ambiguous_browser_scan_is_not_missing_tab_proof(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"}])
        self.accepted_report(jid)
        scan=self.state/"diagnostics"/"recovery-scan.json"
        scan.write_text(json.dumps({
            "at":"2020-01-01T00:00:00+00:00",
            "identified":0,"eligible":0,"recovered":0
        }),encoding="utf-8")
        self.assertEqual(audit.snapshot()["decisions"],{"await_owned_tab":1})
        scan.write_text(json.dumps({
            "at":datetime.now(timezone.utc).isoformat(),
            "identified":1,"eligible":1,"recovered":0
        }),encoding="utf-8")
        self.assertEqual(audit.snapshot()["decisions"],{"await_owned_tab":1})

    def test_send_to_report_duration_is_observed_not_model_compute(self):
        jid=self.active(events=[
            {"stage":"send_clicked"},{"stage":"message_saved"}])
        self.accepted_report(jid)
        row=audit.snapshot()["agents"][0]
        self.assertIsInstance(row["send_to_report_seconds"],(int,float))
        self.assertGreaterEqual(row["send_to_report_seconds"],0)
        self.assertIsNone(row["model_compute_seconds"])
        summary=audit.snapshot()
        self.assertEqual(summary["counts"]["timed_reports"],1)
        self.assertIsInstance(summary["median_send_to_report_seconds"],(int,float))

    def test_unobserved_send_has_unknown_duration(self):
        self.active()
        row=audit.snapshot()["agents"][0]
        self.assertIsNone(row["send_to_report_seconds"])
        self.assertIsNone(row["model_compute_seconds"])

    def test_trial_jobs_are_excluded_from_legacy_scope(self):
        jid=repeat_v064.IDS[0]
        self.active(jid,events=[{"stage":"send_clicked"}])
        self.assertEqual(audit.snapshot()["counts"]["total"],0)


if __name__=="__main__":
    unittest.main(verbosity=2)
