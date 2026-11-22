"""No browser/network dependency: fixed synthetic state and privacy/adversarial tests."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hermes_readonly_doctor as doctor

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
JID = "01234567-89ab-4cde-8901-23456789abcd"
JID2 = "41234567-89ab-4cde-8901-23456789abcd"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "bridge-state"
        for folder in ("queue", "active", "done", "diagnostics", "completion-acks"):
            (self.root/folder).mkdir(parents=True)
        self.heartbeat()

    def tearDown(self):
        self.tmp.cleanup()

    def heartbeat(self, **kw):
        value = {"at": NOW.isoformat(), "version": "0.6.6", "state": "polling", "windows": 1, **kw}
        self.put("diagnostics/extension.json", value)

    def put(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def job(self, folder="active", jid=JID, *, mode=None, status=None):
        payload={"id": jid, "prompt": "SUPER_SECRET_PROMPT_554433", "cookie":"SECRET_COOKIE_0101"}
        if mode:
            payload["completion_mode"] = mode
        if status:
            payload["status"] = status
        self.put(f"{folder}/{jid}.json", payload)
        return jid

    def events(self, stages, jid=JID):
        return self.put(f"diagnostics/{jid}.json", [
            {"at": NOW.isoformat(), "stage": stage,
             "reason": "account_context_changed" if stage == "auth_context_changed" else "",
             "payload": "SUPER_SECRET_PROMPT_554433", "token": "SECRET_COOKIE_0101"}
            for stage in stages])

    def snap(self, **kw):
        return doctor.snapshot(self.root, now=NOW, **kw)

    def codes(self, **kw):
        return [d["code"] for d in self.snap(**kw)["findings"]]

    def test_empty_valid_snapshot_does_not_claim_success(self):
        report = self.snap()
        self.assertEqual(report["counts"], {"queue": 0, "active": 0, "done": 0})
        self.assertFalse(report["browser_or_provider_outcome_trusted"])
        self.assertFalse(report["atomic_across_files"])
        self.assertFalse(report["safe_to_autoretry_unknown_job"])
        self.assertEqual(report["findings"], [])

    def test_hold_is_observed_without_reading_sensitive_contents(self):
        self.put("admission-hold.json", {"reason": "private hold", "prompt": "SECRET"})
        self.job()
        self.events(["tab_opening"])
        out = json.dumps(self.snap())
        self.assertIn("ADMISSION_HOLD_PRESENT", out)
        self.assertNotIn("private hold", out)
        self.assertNotIn("SUPER_SECRET_PROMPT", out)
        self.assertNotIn("SECRET_COOKIE", out)

    def test_old_extension_detected_even_with_fresh_heartbeat(self):
        self.heartbeat(version="0.6.5")
        self.assertIn("EXTENSION_VERSION_MISMATCH", self.codes())

    def test_stale_extension_and_worker_are_hypotheses(self):
        self.heartbeat(at=(NOW - timedelta(seconds=130)).isoformat(), state="paused", windows=0)
        report = self.snap()
        codes = {f["code"]: f["certainty"] for f in report["findings"]}
        self.assertEqual(codes["EXTENSION_HEARTBEAT_STALE"], "hypothesis")
        self.assertIn("EXTENSION_NOT_POLLING", codes)
        self.assertIn("NO_BROWSER_WINDOWS", codes)

    def test_future_heartbeat_untrusted(self):
        self.heartbeat(at=(NOW + timedelta(hours=2)).isoformat())
        self.assertIn("EXTENSION_HEARTBEAT_INVALID", self.codes())

    def test_redacted_per_job_recent_events_support_operator_trace(self):
        self.job()
        path=self.events(["tab_opening", "editor_found", "send_clicked", "message_saved", "unknown"])
        data=json.loads(path.read_text())
        data[-1]["reason"]="MALICIOUS_PRIVATE_MESSAGE"
        path.write_text(json.dumps(data))
        report=self.snap()
        job=report["job_summaries"][0]
        self.assertEqual(job["job_id"],JID)
        self.assertEqual(job["last_stage"],"unknown")
        self.assertLessEqual(len(job["recent_events"]),5)
        output=json.dumps(report)
        self.assertIn("message_saved",output)
        self.assertNotIn("MALICIOUS_PRIVATE_MESSAGE",output)
        self.assertNotIn("SUPER_SECRET_PROMPT",output)

    def test_pre_send_failure_is_not_proof_of_unsent_legacy_job(self):
        self.job()
        self.events(["editor_found", "send_ui_diagnostic"])
        report=self.snap()
        self.assertEqual(report["job_summaries"][0]["last_stage"],"send_ui_diagnostic")
        self.assertFalse(report["safe_to_autoretry_unknown_job"])

    def test_unreported_click_is_not_safe_to_retry(self):
        self.job()
        self.events(["send_clicked"])
        report=self.snap()
        finding=next(d for d in report["findings"] if d["code"]=="POSSIBLY_SENT_AWAITING_RECEIPT")
        self.assertEqual(finding["job_id"], JID)
        self.assertEqual(finding["certainty"], "hypothesis")
        self.assertFalse(report["safe_to_autoretry_unknown_job"])

    def test_ambiguous_send_and_account_dispute_are_critical(self):
        self.job()
        self.events(["send_clicked", "unknown", "auth_context_changed"])
        report=self.snap()
        codes={r["code"] for r in report["findings"]}
        self.assertIn("AMBIGUOUS_EXTERNAL_OUTCOME",codes)
        self.assertIn("ACCOUNT_CONTEXT_DISPUTED",codes)
        self.assertTrue(all(r["severity"]=="critical" for r in report["findings"]
                            if r["code"] in ("AMBIGUOUS_EXTERNAL_OUTCOME", "ACCOUNT_CONTEXT_DISPUTED")))

    def test_done_report_without_ack_blocked(self):
        self.job("done",mode="report",status="done")
        self.events(["message_saved", "done"])
        self.assertIn("DONE_REPORT_ACK_UNVERIFIED",self.codes())

    def test_acked_done_record_not_canonical_proof(self):
        self.job("done",mode="report",status="done")
        self.events(["message_saved", "done", "report_acked"])
        self.put(f"completion-acks/{JID}.json", {"status":"REPORT_ACKED", "secret":"DONOTSHOW"})
        out=self.snap()
        self.assertNotIn("DONE_REPORT_ACK_UNVERIFIED",{d["code"] for d in out["findings"]})
        self.assertFalse(out["browser_or_provider_outcome_trusted"])
        self.assertNotIn("DONOTSHOW",json.dumps(out))

    def test_duplicate_job_filename_is_hypothesis_of_race(self):
        self.job("queue")
        self.job("active")
        self.events(["tab_opening"])
        self.assertIn("STATE_DUPLICATE_OR_TRANSITION",self.codes())

    def test_active_job_telemetry_silence_is_hypothesis(self):
        self.job()
        self.events(["tab_opening"])
        e=self.root/f"diagnostics/{JID}.json"
        e.write_text(json.dumps([{"stage":"tab_opening", "at":(NOW-timedelta(hours=1)).isoformat()}]))
        report=self.snap()
        self.assertIn("ACTIVE_JOB_TELEMETRY_SILENCE",{d["code"] for d in report["findings"]})

    def test_malicious_json_duplicate_key_and_unbounded_file_fail_closed(self):
        self.job()
        p=self.root/f"diagnostics/{JID}.json"
        p.write_text('[{"stage":"send_clicked","stage":"tab_closed"}]')
        self.assertIn("JOB_TELEMETRY_UNAVAILABLE",self.codes())
        p.write_bytes(b"X" * (doctor.MAX_FILE_BYTES+10))
        self.assertIn("JOB_TELEMETRY_UNAVAILABLE",self.codes())

    def test_symlink_is_not_followed_and_does_not_leak(self):
        self.job()
        secret=self.root/"private-file.json"
        secret.write_text('"SECRET_PRIVATE_KEY"')
        link=self.root/f"diagnostics/{JID}.json"
        try:
            link.symlink_to(secret)
        except (OSError,NotImplementedError):
            self.skipTest("symlinks not available")
        out=json.dumps(self.snap())
        self.assertIn("JOB_TELEMETRY_UNAVAILABLE",out)
        self.assertNotIn("SECRET_PRIVATE_KEY",out)

    def test_unexpected_job_file_name_not_echoed(self):
        self.put("active/password_private.json", {"token":"SECRET"})
        out=json.dumps(self.snap())
        self.assertIn("INVALID_JOB_FILENAME",out)
        self.assertNotIn("password_private",out)

    def test_no_state_tree_is_error_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(doctor.MonitorError):
                doctor.snapshot(Path(tmp)/"missing", now=NOW)

    def test_bad_heartbeat_file_does_not_crash(self):
        (self.root/"diagnostics/extension.json").write_bytes(b"bad json"*100)
        report=self.snap()
        self.assertIn("EXTENSION_HEARTBEAT_UNAVAILABLE",{d["code"] for d in report["findings"]})
        self.assertIsNone(report["extension"]["version"])

    def test_cli_unknown_invalid_input_is_redacted(self):
        with io.StringIO() as buf, redirect_stdout(buf):
            rc=doctor.main(["--state",str(self.root/"non-existent")])
            out=buf.getvalue()
        self.assertEqual(rc,2)
        self.assertNotIn(str(self.root),out)
        self.assertIn("OBSERVER_INPUT_INVALID",out)

    def test_cli_returns_critical_without_mutation(self):
        self.job()
        self.events(["auth_context_changed"])
        before=sorted((str(p.relative_to(self.root)),p.stat().st_size)
                      for p in self.root.rglob("*.json"))
        with io.StringIO() as buf, redirect_stdout(buf):
            rc=doctor.main(["--state",str(self.root)])
            out=buf.getvalue()
        after=sorted((str(p.relative_to(self.root)),p.stat().st_size)
                     for p in self.root.rglob("*.json"))
        # CLI uses current time; no direct reliance on hard-coded staleness.
        self.assertEqual(rc,1)
        self.assertEqual(before,after)
        self.assertIn("ACCOUNT_CONTEXT_DISPUTED",out)

    # Additional privacy/resource regressions.
    def test_version_field_redacted_if_not_semantic_version(self):
        self.heartbeat(version="SECRET_PASSWORD")
        body=json.dumps(self.snap())
        self.assertNotIn("SECRET_PASSWORD",body)
        self.assertIn("EXTENSION_VERSION_MISMATCH",body)

    def test_bounded_directory_scan_does_not_claim_completeness(self):
        for i in range(doctor.MAX_FILES_PER_STATE+1):
            jid=f"{i+1:08x}-89ab-4cde-8901-23456789abcd"
            self.job("queue",jid=jid)
        data=self.snap()
        self.assertIn("STATE_SCAN_TRUNCATED",[x["code"] for x in data["findings"]])
        self.assertLessEqual(data["job_count_scanned"],doctor.MAX_FILES_PER_STATE)

if __name__=="__main__": unittest.main()
