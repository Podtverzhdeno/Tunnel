"""Hermetic v0.6.5 repeat-lane tests with real isolated HTTP handling."""
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

import bridge
import enqueue_completion as ec
import repeat_v064 as trial


class RepeatTrialTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project=Path(temp.name)/"project"
        self.root=self.project/"web-companion"
        self.state=self.root/"bridge-state"
        self.area=self.state/"repeat-v064-20261103"
        self.queue=self.area/"queue"
        self.active=self.state/"active"
        self.done=self.state/"done"
        self.legacy=self.state/"queue"
        self.acks=self.state/"completion-acks"
        self.reports=self.project/"agent-reports"
        self.diag=self.state/"diagnostics"
        self.hold=self.state/"admission-hold.json"
        self.briefs=self.project/"agent-briefs"
        self.work=self.project/"experiments"/"repeat-v064-20261103"
        for folder in (self.queue,self.active,self.done,self.legacy,self.acks,
                       self.reports,self.diag,self.briefs):
            folder.mkdir(parents=True,exist_ok=True)
        self.hold.write_text("old-load25-stays-held",encoding="utf-8")
        self.legacy.joinpath("old-pending.json").write_text(
            '{"id":"old-pending","prompt":"do-not-replay"}',encoding="utf-8")
        hb={"at":datetime.now(timezone.utc).isoformat(),
            "version":"0.6.6","state":"polling","windows":1}
        self.diag.joinpath("extension.json").write_text(json.dumps(hb))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        paths={
            (trial,"ROOT"):self.root,(trial,"PROJECT"):self.project,
            (trial,"STATE"):self.state,(trial,"LANE"):self.area,
            (trial,"QUEUE"):self.queue,(trial,"PERMIT"):self.area/"permit.json",
            (trial,"ACTIVE"):self.active,(trial,"DONE"):self.done,
            (trial,"LEGACY_QUEUE"):self.legacy,(trial,"HOLD"):self.hold,
            (trial,"BRIEFS"):self.briefs,(trial,"WORK"):self.work,
            (trial,"DIAG"):self.diag,
            (ec,"ROOT"):self.root,(ec,"PROJECT"):self.project,
            (ec,"QUEUE"):self.legacy,(ec,"ACTIVE"):self.active,
            (ec,"DONE"):self.done,
            (bridge,"STATE"):self.state,(bridge,"QUEUE"):self.legacy,
            (bridge,"ACTIVE"):self.active,(bridge,"DONE"):self.done,
            (bridge,"DIAG"):self.diag,(bridge,"ADMISSION_HOLD"):self.hold,
            (bridge,"ACKS"):self.acks,(bridge,"REPORTS"):self.reports,
        }
        for (ob,attr),value in paths.items():
            self.stack.enter_context(patch.object(ob,attr,value))
        self.initial_hold=hashlib.sha256(self.hold.read_bytes()).hexdigest()

    def test_new_experiment_has_no_permit_by_default(self):
        self.assertIsNone(trial.claim_held_trial())
        self.assertFalse(trial.preview()["permit_valid"])
        self.assertEqual(len(list(self.legacy.glob("*.json"))),1)

    def test_prepare_one_shot_four_and_idempotence(self):
        result=trial.prepare()
        self.assertTrue(result["ok"])
        self.assertEqual(result["prepared"],4)
        self.assertEqual(len(list(self.queue.glob("*.json"))),4)
        self.assertEqual(len(list(self.legacy.glob("*.json"))),1)
        self.assertEqual(trial.prepare()["existing"],True)
        self.assertEqual(len(list(self.queue.glob("*.json"))),4)
        self.assertEqual(hashlib.sha256(self.hold.read_bytes()).hexdigest(),
                         self.initial_hold)
        self.assertTrue(all((self.briefs/(r+".md")).exists() for r in trial.ROLES))

    def test_claim_four_then_stops_without_legacy_consumption(self):
        trial.prepare()
        claimed=[trial.claim_held_trial() for _ in range(4)]
        self.assertEqual([x["id"] for x in claimed],list(trial.IDS))
        self.assertTrue(all(x["completion_mode"]=="report" for x in claimed))
        self.assertEqual(len(list(self.active.glob("*.json"))),4)
        self.assertIsNone(trial.claim_held_trial())
        self.assertEqual(len(list(self.legacy.glob("*.json"))),1)
        self.assertEqual(hashlib.sha256(self.hold.read_bytes()).hexdigest(),
                         self.initial_hold)

    def test_cap_respects_preexisting_seventeen_and_eight(self):
        trial.prepare()
        for i in range(17):
            (self.active/("old-%d.json"%i)).write_text("{}")
        self.assertEqual(len(list(self.active.glob("*.json"))),17)
        for _ in range(4):
            self.assertIsNotNone(trial.claim_held_trial())
        self.assertEqual(len(list(self.active.glob("*.json"))),21)
        self.assertIsNone(trial.claim_held_trial())

    def test_expired_permit_does_not_restart(self):
        trial.prepare()
        p=trial.PERMIT
        m=json.loads(p.read_text(encoding="utf-8"))
        m["expires_at"]=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()
        p.write_text(json.dumps(m),encoding="utf-8")
        self.assertIsNone(trial.claim_held_trial())
        self.assertEqual(len(list(self.queue.glob("*.json"))),4)

    def test_job_content_mutation_blocks_claim(self):
        trial.prepare()
        p=self.queue/(trial.IDS[0]+".json")
        d=json.loads(p.read_text(encoding="utf-8"))
        d["prompt"]="ATTACK"
        p.write_text(json.dumps(d),encoding="utf-8")
        candidate=trial.claim_held_trial()
        self.assertEqual(candidate["id"],trial.IDS[1])
        self.assertTrue(p.exists(),"tampered job stays isolated, unclaimed")

    def test_incompatible_browser_cannot_claim(self):
        trial.prepare()
        p=self.diag/"extension.json"
        d=json.loads(p.read_text(encoding="utf-8"))
        d["version"]="0.6.3"
        p.write_text(json.dumps(d))
        self.assertIsNone(trial.claim_held_trial())

    def test_hold_missing_blocks_preparation_and_claims(self):
        with self.assertRaises(trial.TrialError):
            self.hold.unlink()
            trial.prepare()
        self.hold.write_text("old-load25-stays-held")
        trial.prepare()
        self.hold.unlink()
        self.assertIsNone(trial.claim_held_trial())

    def test_old_main_queue_never_replayed_via_http(self):
        trial.prepare()
        with bridge.Server(("127.0.0.1",0),bridge.Handler) as server:
            server.bridge_token="test-secret-1234567890123456789012345678"
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                def next_request():
                    req=urllib.request.Request(
                        "http://127.0.0.1:"+str(server.server_port)+"/v1/next",
                        headers={"X-Chat-Smoke-Key":server.bridge_token,
                                 "Origin":"chrome-extension://test"})
                    with urllib.request.urlopen(req,timeout=3) as resp:
                        return json.load(resp)
                observed=[next_request()["job"]["id"] for i in range(4)]
                self.assertEqual(observed,list(trial.IDS))
                fifth=next_request()
                self.assertIsNone(fifth["job"])
                self.assertEqual(fifth["reason"],"admission_held")
                self.assertTrue((self.legacy/"old-pending.json").exists())
                self.assertEqual(len(list(self.active.glob("*.json"))),4)
            finally:
                server.shutdown()
                thread.join(3)

    def test_report_distinguishes_unexpected_from_acknowledged_closure(self):
        import repeat_v064_report as reporter
        unexpected_id, expected_id = trial.IDS[:2]
        (self.active/(unexpected_id+".json")).write_text(
            json.dumps({"id":unexpected_id,"completion_mode":"report"}),encoding="utf-8")
        (self.done/(expected_id+".json")).write_text(
            json.dumps({"id":expected_id,"completion_mode":"report","status":"submitted"}),
            encoding="utf-8")
        for jid, reason in ((unexpected_id,"unexpected_close"),(expected_id,None)):
            report=self.reports/("completion-"+jid+".md")
            report.write_text("Job ID: "+jid+"\\n## Outcome\\nOK\\n## Evidence\\nverified",
                              encoding="utf-8")
            sha=hashlib.sha256(report.read_bytes()).hexdigest()
            ack_id="f"*32
            (self.acks/(jid+".json")).write_text(json.dumps({
                "job_id":jid,"status":"REPORT_ACKED","artifact_sha256":sha,
                "ack_id":ack_id
            }),encoding="utf-8")
            if jid==expected_id:
                (self.done/(jid+".json")).write_text(json.dumps({
                    "id":jid,"status":"done","url":"https://chatgpt.com/c/safe",
                    "report_path":"agent-reports/completion-"+jid+".md",
                    "report_sha256":sha,"report_ack_id":ack_id
                }),encoding="utf-8")
            event={"at":datetime.now(timezone.utc).isoformat(),"stage":"tab_closed"}
            if reason:event["reason"]=reason
            now=datetime.now(timezone.utc).isoformat()
            (self.diag/(jid+".json")).write_text(json.dumps([
                {"at":now,"stage":"send_clicked"},
                {"at":now,"stage":"message_saved"},event
            ]),encoding="utf-8")
        rows={r["job_id"]:r for r in reporter.snapshot()["agents"]}
        unexpected,expected=rows[unexpected_id],rows[expected_id]
        self.assertTrue(unexpected["tab_closed"])
        self.assertTrue(unexpected["tab_closed_unexpected"])
        self.assertFalse(unexpected["tab_closed_expected"])
        self.assertFalse(unexpected["acknowledged_closure"])
        self.assertTrue(expected["tab_closed_expected"])
        self.assertFalse(expected["tab_closed_unexpected"])
        self.assertTrue(expected["acknowledged_closure"])
        counts=reporter.snapshot()["counts"]
        self.assertEqual(counts["closed"],2)
        self.assertEqual(counts["closed_unexpected"],1)
        self.assertEqual(counts["acknowledged_closure"],1)

    def test_reconciliation_decisions_fail_closed(self):
        import repeat_v064_report as reporter
        baseline=[{"stage":"send_clicked"},{"stage":"message_saved"}]
        table=[
            ("active",True,True,False,False,baseline,
             "await_browser_identity"),
            ("active",True,True,False,True,baseline,
             "manual_unexpected_close"),
            ("active",True,True,True,False,baseline,
             "manual_result_missing"),
            ("active",False,True,False,False,baseline,
             "manual_report_disputed"),
            ("active",True,True,False,False,
             [{"stage":"message_saved"},{"stage":"send_clicked"}],
             "manual_send_uncertain"),
            ("active",True,True,False,False,baseline+[{"stage":"unknown"}],
             "manual_identity_or_unknown"),
            ("active",True,True,False,False,
             baseline+[{"stage":"tab_binding_disputed"}],
             "manual_identity_or_unknown"),
            ("done",True,True,True,False,baseline,
             "local_pipeline_confirmed"),
            ("done",True,True,False,True,baseline,
             "manual_unexpected_close"),
            ("done",True,True,False,False,baseline,
             "inspect_close_only_eligibility"),
            ("done",False,True,True,False,baseline,
             "manual_report_disputed"),
            ("queue",False,False,False,False,[],
             "await_dispatch"),
            ("missing",True,True,False,False,baseline,
             "manual_missing_state")
        ]
        for state,ack,report,expected,unexpected,events,result in table:
            with self.subTest(state=state,events=events,result=result):
                actual=reporter.reconciliation_decision(
                    state=state,valid_ack=ack,has_report=report,
                    expected_close=expected,unexpected_close=unexpected,
                    events=events,strict_done_receipt=state=="done")
                self.assertEqual(actual,result)

    def test_latest_save_must_follow_latest_send(self):
        import repeat_v064_report as reporter
        self.assertFalse(reporter.saved_after_latest_send([]))
        self.assertFalse(reporter.saved_after_latest_send([
            {"stage":"message_saved"},{"stage":"send_clicked"}]))
        self.assertTrue(reporter.saved_after_latest_send([
            {"stage":"send_clicked"},{"stage":"message_saved"}]))
        self.assertFalse(reporter.saved_after_latest_send([
            {"stage":"send_clicked"},{"stage":"message_saved"},
            {"stage":"send_clicked"}]))

    def test_unexpected_close_cannot_be_erased_by_later_close(self):
        import repeat_v064_report as reporter
        jid=trial.IDS[0]
        (self.done/(jid+".json")).write_text(
            json.dumps({"id":jid,"status":"done","completion_mode":"report"}),encoding="utf-8")
        report=self.reports/("completion-"+jid+".md")
        report.write_text("Job ID: "+jid,encoding="utf-8")
        (self.acks/(jid+".json")).write_text(json.dumps({
            "status":"REPORT_ACKED","job_id":jid,
            "artifact_sha256":hashlib.sha256(report.read_bytes()).hexdigest()
        }),encoding="utf-8")
        stages=[
            {"stage":"send_clicked"},{"stage":"message_saved"},
            {"stage":"tab_closed","reason":"unexpected_close"},
            {"stage":"tab_closed"}]
        (self.diag/(jid+".json")).write_text(
            json.dumps([{"at":datetime.now(timezone.utc).isoformat(),**x}
                        for x in stages]),encoding="utf-8")
        row=next(x for x in reporter.snapshot()["agents"] if x["job_id"]==jid)
        self.assertTrue(row["tab_closed_unexpected"])
        self.assertFalse(row["tab_closed_expected"])
        self.assertFalse(row["acknowledged_closure"])
        self.assertEqual(row["reconciliation_decision"],"manual_unexpected_close")

    def test_conflicting_state_files_require_manual_reconciliation(self):
        import repeat_v064_report as reporter
        jid=trial.IDS[0]
        for folder in (self.active,self.done):
            (folder/(jid+".json")).write_text(json.dumps({"id":jid}),encoding="utf-8")
        (self.diag/(jid+".json")).write_text(json.dumps([
            {"at":datetime.now(timezone.utc).isoformat(),"stage":"send_clicked"},
            {"at":datetime.now(timezone.utc).isoformat(),"stage":"message_saved"}]),
            encoding="utf-8")
        row=next(x for x in reporter.snapshot()["agents"] if x["job_id"]==jid)
        self.assertEqual(row["state"],"conflict")
        self.assertEqual(row["reconciliation_decision"],"manual_multiple_state_files")

    def test_oversized_report_cannot_satisfy_ack(self):
        import repeat_v064_report as reporter
        jid=trial.IDS[0]
        (self.active/(jid+".json")).write_text(
            json.dumps({"id":jid,"completion_mode":"report"}),encoding="utf-8")
        huge=b"x"*1_048_577
        (self.reports/("completion-"+jid+".md")).write_bytes(huge)
        (self.acks/(jid+".json")).write_text(json.dumps({
            "status":"REPORT_ACKED","job_id":jid,
            "artifact_sha256":hashlib.sha256(huge).hexdigest()
        }),encoding="utf-8")
        (self.diag/(jid+".json")).write_text(json.dumps([
            {"at":datetime.now(timezone.utc).isoformat(),"stage":"send_clicked"},
            {"at":datetime.now(timezone.utc).isoformat(),"stage":"message_saved"}]),
            encoding="utf-8")
        row=next(x for x in reporter.snapshot()["agents"] if x["job_id"]==jid)
        self.assertTrue(row["report_exists"])
        self.assertFalse(row["report_sha_matches_ack"])
        self.assertEqual(row["reconciliation_decision"],"manual_report_disputed")

    def test_done_without_strict_receipt_metadata_is_not_complete(self):
        import repeat_v064_report as reporter
        jid=trial.IDS[0]
        (self.done/(jid+".json")).write_text(
            json.dumps({"id":jid,"status":"done"}),encoding="utf-8")
        report=self.reports/("completion-"+jid+".md")
        report.write_text("Job ID: "+jid,encoding="utf-8")
        (self.acks/(jid+".json")).write_text(json.dumps({
            "status":"REPORT_ACKED","job_id":jid,
            "artifact_sha256":hashlib.sha256(report.read_bytes()).hexdigest()
        }),encoding="utf-8")
        now=datetime.now(timezone.utc).isoformat()
        (self.diag/(jid+".json")).write_text(json.dumps([
            {"at":now,"stage":"send_clicked"},
            {"at":now,"stage":"message_saved"},
            {"at":now,"stage":"tab_closed"}]),encoding="utf-8")
        row=next(x for x in reporter.snapshot()["agents"] if x["job_id"]==jid)
        self.assertFalse(row["acknowledged_closure"])
        self.assertEqual(row["reconciliation_decision"],"manual_receipt_disputed")

    def test_active_recovery_eligibility_uses_server_gate(self):
        import repeat_v064_report as reporter
        jid=trial.IDS[0]
        # Missing report_name: a visually plausible ACTIVE job is ineligible.
        (self.active/(jid+".json")).write_text(
            json.dumps({"id":jid,"completion_mode":"report"}),encoding="utf-8")
        report=self.reports/("completion-"+jid+".md")
        report.write_text("Job ID: "+jid,encoding="utf-8")
        (self.acks/(jid+".json")).write_text(json.dumps({
            "status":"REPORT_ACKED","job_id":jid,
            "artifact_sha256":hashlib.sha256(report.read_bytes()).hexdigest()
        }),encoding="utf-8")
        now=datetime.now(timezone.utc).isoformat()
        (self.diag/(jid+".json")).write_text(json.dumps([
            {"at":now,"stage":"send_clicked"},
            {"at":now,"stage":"message_saved"}]),encoding="utf-8")
        row=next(x for x in reporter.snapshot()["agents"] if x["job_id"]==jid)
        self.assertFalse(row["server_recovery_eligible"])
        self.assertEqual(row["server_recovery_reason"],"invalid_job")
        self.assertEqual(row["reconciliation_decision"],"manual_recovery_ineligible")

if __name__=="__main__":
    unittest.main(verbosity=2)
