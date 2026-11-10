"""Non-destructive crash recovery diagnostics for generated OpenSpec artifacts."""
from contextlib import closing, redirect_stdout
import io
import json
from unittest.mock import patch
import ops
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import spec_tools as spec
from spec_approval_queue import ApprovalQueue, QueueError, RECONCILE_AFTER_SEC
from test_spec_tools import sample


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=self.root/"example-project"
        self.project.mkdir()
        self.manifest_path=self.root/"brief.json"
        self.manifest_path.write_text(json.dumps(sample(),ensure_ascii=False),encoding="utf-8")
        self.now=123456.0
        self.queue=ApprovalQueue(self.root/"requests.db",self.root,lambda:self.now)
        self.brief=spec.read_manifest("brief.json",self.root)
        self.hash=spec.preview(self.brief,self.root)["plan_sha256"]
        self.id=self.queue.prepare("brief.json",self.hash)["request_id"]

    def expected_files(self):
        return spec.build(self.brief,self.project)

    def write_expected(self,count=None):
        files=list(self.expected_files().items())
        for relative,content in files[:count] if count is not None else files:
            target=self.project/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content,encoding="utf-8",newline="\n")
        return files

    def test_prepared_request_shows_all_missing(self):
        diagnosis=self.queue.diagnose(self.id)
        self.assertEqual(diagnosis["counts"]["missing"],len(self.expected_files()))
        self.assertEqual(diagnosis["counts"]["matching"],0)
        self.assertFalse(diagnosis["safe_to_retry"])
        self.assertFalse((self.project/"openspec").exists())

    def test_partial_crash_shows_missing_and_matching(self):
        self.write_expected(count=2)
        diagnosis=self.queue.diagnose(self.id)
        self.assertEqual(diagnosis["counts"]["matching"],2)
        self.assertGreater(diagnosis["counts"]["missing"],0)
        self.assertFalse(diagnosis["all_files_match"])

    def test_modified_output_detected_without_contents(self):
        files=self.write_expected()
        target=self.project/files[0][0]
        target.write_text("changed by user",encoding="utf-8")
        diagnosis=self.queue.diagnose(self.id)
        self.assertEqual(diagnosis["counts"]["changed"],1)
        self.assertNotIn("changed by user",json.dumps(diagnosis))
        self.assertFalse(diagnosis["safe_to_retry"])

    def test_complete_exact_fingerprints(self):
        count=len(self.expected_files())
        self.write_expected()
        diagnosis=self.queue.diagnose(self.id)
        self.assertTrue(diagnosis["all_files_match"])
        self.assertEqual(diagnosis["expected_files"],count)
        self.assertFalse(diagnosis["safe_to_retry"])

    def test_old_running_request_requires_reconciliation(self):
        with closing(self.queue._connect()) as db:
            db.execute("UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",
                      (self.now-RECONCILE_AFTER_SEC-1,self.id))
        self.assertEqual(self.queue.status(self.id)["state"],"reconciliation_required")
        diagnosis=self.queue.diagnose(self.id)
        self.assertEqual(diagnosis["state"],"reconciliation_required")
        with self.assertRaises(QueueError) as cm:
            self.queue.execute(self.id)
        self.assertEqual(cm.exception.code,"RECONCILIATION_REQUIRED")

    def test_legacy_schema_upgrades_without_deleting_records(self):
        db=self.root/"legacy.db"
        with closing(sqlite3.connect(db)) as con:
            con.execute("""CREATE TABLE spec_requests(
               request_id TEXT PRIMARY KEY,manifest_path TEXT,manifest_sha256 TEXT,
               project_path TEXT,change_name TEXT,plan_sha256 TEXT,state TEXT,
               created_at REAL,expires_at REAL,decision_at REAL,result_json TEXT
            )""")
            con.commit()
        upgraded=ApprovalQueue(db,self.root,lambda:self.now)
        with closing(upgraded._connect()) as conn:
            names={r[1] for r in conn.execute("PRAGMA table_info(spec_requests)")}
        self.assertTrue({"running_at","artifact_json"}.issubset(names))

    def test_cli_diagnose_returns_structured_json(self):
        with patch("spec_approval_queue.ApprovalQueue", return_value=self.queue):
            output=io.StringIO()
            with redirect_stdout(output):
                code=ops.main(["spec_request_diagnose",self.id])
        self.assertEqual(code,0)
        payload=json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["safe_to_retry"])
        self.assertEqual(payload["data"]["counts"]["missing"],10)

    def test_cli_diagnose_unknown_request_is_error(self):
        with patch("spec_approval_queue.ApprovalQueue", return_value=self.queue):
            output=io.StringIO()
            with redirect_stdout(output):
                code=ops.main(["spec_request_diagnose","a"*32])
        self.assertEqual(code,1)
        payload=json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"],"REQUEST_NOT_FOUND")

    def test_no_artifacts_are_deleted_during_diagnosis(self):
        self.write_expected(count=1)
        before=self.expected_files()
        existing=self.project/next(iter(before))
        prior=existing.read_bytes()
        self.queue.diagnose(self.id)
        self.assertEqual(existing.read_bytes(),prior)

    def _mark_stale(self):
        with closing(self.queue._connect()) as db:
            db.execute(
                "UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",
                (self.now - RECONCILE_AFTER_SEC - 10, self.id),
            )

    def _terminal(self, text=""):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        return Terminal(text)

    def test_manual_reconcile_stale_complete_without_writing_files(self):
        expected_count = len(self.expected_files())
        self.write_expected()
        source = self.project / next(iter(self.expected_files()))
        initial = source.read_bytes()
        self._mark_stale()
        with patch("spec_approval_queue.spec.validate", return_value={
            "validated": True, "sdd": {"validated": True}
        }) as validator:
            result = self.queue.reconcile_console(
                self.id,
                stdin=self._terminal("RECONCILE " + self.id[:8] + "\n"),
                stdout=self._terminal(),
            )
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["files"], expected_count)
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(source.read_bytes(), initial)
        replay = self.queue.execute(self.id)
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["reconciled"])

    def test_manual_reconcile_rejects_noninteractive(self):
        self.write_expected()
        self._mark_stale()
        with self.assertRaises(QueueError) as context:
            self.queue.reconcile_console(
                self.id,
                stdin=io.StringIO("RECONCILE " + self.id[:8] + "\n"),
                stdout=io.StringIO(),
            )
        self.assertEqual(context.exception.code, "INTERACTIVE_REQUIRED")
        self.assertEqual(self.queue.status(self.id)["state"], "reconciliation_required")

    def test_manual_reconcile_requires_stale_run(self):
        self.write_expected()
        with closing(self.queue._connect()) as db:
            db.execute("UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",
                       (self.now, self.id))
        with self.assertRaises(QueueError) as context:
            self.queue.reconcile_console(
                self.id, stdin=self._terminal(), stdout=self._terminal()
            )
        self.assertEqual(context.exception.code, "RECONCILIATION_DENIED")

    def test_manual_reconcile_rejects_partial_artifacts(self):
        self.write_expected(count=1)
        self._mark_stale()
        with patch("spec_approval_queue.spec.validate") as validator:
            with self.assertRaises(QueueError) as context:
                self.queue.reconcile_console(
                    self.id,
                    stdin=self._terminal("RECONCILE " + self.id[:8] + "\n"),
                    stdout=self._terminal(),
                )
        self.assertEqual(context.exception.code, "ARTIFACTS_INCOMPLETE")
        validator.assert_not_called()
        self.assertEqual(self.queue.status(self.id)["state"], "reconciliation_required")

    def test_manual_reconcile_fails_strict_validation(self):
        self.write_expected()
        self._mark_stale()
        with patch("spec_approval_queue.spec.validate", side_effect=spec.SpecError(
            "VALIDATION_FAILED", "not valid"
        )):
            with self.assertRaises(QueueError) as context:
                self.queue.reconcile_console(
                    self.id,
                    stdin=self._terminal("RECONCILE " + self.id[:8] + "\n"),
                    stdout=self._terminal(),
                )
        self.assertEqual(context.exception.code, "VALIDATION_FAILED")

    def test_manual_reconcile_denial_keeps_running(self):
        self.write_expected()
        self._mark_stale()
        with patch("spec_approval_queue.spec.validate", return_value={
            "validated": True, "sdd": {"validated": True}
        }):
            with self.assertRaises(QueueError) as context:
                self.queue.reconcile_console(
                    self.id, stdin=self._terminal("NO\n"), stdout=self._terminal()
                )
        self.assertEqual(context.exception.code, "RECONCILIATION_REJECTED")
        self.assertEqual(self.queue.status(self.id)["state"], "reconciliation_required")

    def test_manual_reconcile_detects_edit_while_user_approves(self):
        files = self.write_expected()
        self._mark_stale()
        target = self.project / files[0][0]
        class EditingTerminal(io.StringIO):
            def isatty(self):
                return True
            def readline(inner):
                target.write_text("edited during review", encoding="utf-8")
                return "RECONCILE " + self.id[:8] + "\n"
        with patch("spec_approval_queue.spec.validate", return_value={
            "validated": True, "sdd": {"validated": True}
        }):
            with self.assertRaises(QueueError) as context:
                self.queue.reconcile_console(
                    self.id, stdin=EditingTerminal(), stdout=self._terminal()
                )
        self.assertEqual(context.exception.code, "ARTIFACTS_INCOMPLETE")
        self.assertEqual(self.queue.status(self.id)["state"], "reconciliation_required")


if __name__=="__main__":
    unittest.main()
