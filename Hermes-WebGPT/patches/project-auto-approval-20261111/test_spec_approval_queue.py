"""Durable OpenSpec creation approvals: denial, replay and recovery tests."""
import io
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from spec_approval_queue import ApprovalQueue, QueueError
from test_spec_tools import sample
import spec_tools as spec


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ApprovalQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "example-project").mkdir()
        self.manifest = "brief.json"
        (self.root / self.manifest).write_text(json.dumps(sample(), ensure_ascii=False),encoding="utf-8")
        self.now = 1000.0
        self.queue = ApprovalQueue(self.root / "state.sqlite3",root=self.root,clock=lambda:self.now)
        self.plan_hash = spec.preview(spec.read_manifest(self.manifest,self.root),self.root)["plan_sha256"]

    def prepare(self):
        return self.queue.prepare(self.manifest,self.plan_hash)["request_id"]

    def approve(self,request_id,accept=True):
        inp=Terminal("APPROVE "+request_id[:8]+"\n" if accept else "NO\n")
        out=Terminal()
        result=self.queue.review_console(request_id,stdin=inp,stdout=out)
        self.assertIn("Target files",out.getvalue())
        self.assertIn("example-project",out.getvalue())
        return result

    def test_prepare_no_project_mutation(self):
        request=self.prepare()
        self.assertEqual(self.queue.status(request)["state"],"pending")
        self.assertFalse((self.root/"example-project"/"openspec").exists())

    def test_no_tty_no_approval(self):
        request=self.prepare()
        with self.assertRaises(QueueError) as e:
            self.queue.review_console(request,stdin=io.StringIO("APPROVE "+request[:8]),stdout=io.StringIO())
        self.assertEqual(e.exception.code,"INTERACTIVE_REQUIRED")
        self.assertEqual(self.queue.status(request)["state"],"pending")

    def test_wrong_phrase_denies(self):
        request=self.prepare()
        self.assertEqual(self.approve(request,False)["state"],"denied")
        with self.assertRaises(QueueError):
            self.queue.execute(request)

    def test_expires(self):
        request=self.prepare()
        self.now+=901
        self.assertEqual(self.queue.status(request)["state"],"expired")
        with self.assertRaises(QueueError):
            self.approve(request)

    def test_approval_expires_before_execution(self):
        request=self.prepare()
        self.approve(request)
        self.now+=901
        with self.assertRaises(QueueError) as error:
            self.queue.execute(request)
        self.assertEqual(error.exception.code,"APPROVAL_EXPIRED")

    def test_manifest_change_requires_new_preview(self):
        request=self.prepare()
        source=self.root / self.manifest
        source.write_text(source.read_text(encoding="utf-8")+"\n",encoding="utf-8")
        with self.assertRaises(QueueError) as e:
            self.approve(request)
        self.assertEqual(e.exception.code,"PREVIEW_CHANGED")
        self.assertEqual(self.queue.status(request)["state"],"pending")

    def test_invalid_plan_hash(self):
        with self.assertRaises(QueueError):
            self.queue.prepare(self.manifest,"0"*64)

    def test_execute_only_when_approved(self):
        request=self.prepare()
        with self.assertRaises(QueueError) as e:
            self.queue.execute(request)
        self.assertEqual(e.exception.code,"APPROVAL_REQUIRED")

    def test_executes_once_and_replays(self):
        request=self.prepare()
        self.approve(request)
        with patch("spec_approval_queue.spec.create",return_value={
            "files":["a.md"],"upstream":{"validated":True}}) as create:
            first=self.queue.execute(request)
            second=self.queue.execute(request)
        self.assertTrue(first["created"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        create.assert_called_once()
        self.assertEqual(self.queue.status(request)["state"],"completed")

    def test_failure_not_retried(self):
        request=self.prepare()
        self.approve(request)
        with patch("spec_approval_queue.spec.create",side_effect=spec.SpecError("VALIDATION_FAILED","bad")) as create:
            with self.assertRaises(QueueError):
                self.queue.execute(request)
        self.assertEqual(self.queue.status(request)["state"],"failed")
        with self.assertRaises(QueueError):
            self.queue.execute(request)
        create.assert_called_once()

    def test_concurrent_state_running_denies_second(self):
        request=self.prepare()
        self.approve(request)
        with closing(self.queue._connect()) as db:
            db.execute("UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",(self.now,request))
        with self.assertRaises(QueueError) as error:
            self.queue.execute(request)
        self.assertEqual(error.exception.code,"IN_PROGRESS")

    def test_invalid_request_id(self):
        with self.assertRaises(QueueError):
            self.queue.status("../../etc/passwd")

    def test_stale_running_requires_manual_reconciliation(self):
        request=self.prepare()
        self.approve(request)
        with closing(self.queue._connect()) as db:
            db.execute("UPDATE spec_requests SET state='running',running_at=? WHERE request_id=?",
                       (self.now,request))
        self.now+=301
        self.assertEqual(self.queue.status(request)["state"],"reconciliation_required")
        with self.assertRaises(QueueError) as err:
            self.queue.execute(request)
        self.assertEqual(err.exception.code,"RECONCILIATION_REQUIRED")
        self.assertFalse((self.root/"example-project/openspec").exists())

    def test_queue_limit_prevents_unbounded_active_requests(self):
        with patch("spec_approval_queue.MAX_ACTIVE_REQUESTS",1):
            first=self.prepare()
            self.assertEqual(self.queue.status(first)["state"],"pending")
            with self.assertRaises(QueueError) as err:
                self.prepare()
            self.assertEqual(err.exception.code,"QUEUE_FULL")
            self.now+=901
            second=self.prepare()
            self.assertNotEqual(first,second)

    def test_completed_result_persists_across_new_queue_instance(self):
        request=self.prepare()
        self.approve(request)
        with patch("spec_approval_queue.spec.create",return_value={
            "files":["a.md"],"upstream":{"validated":True}}) as create:
            first=self.queue.execute(request)
            restarted=ApprovalQueue(self.root/"state.sqlite3",root=self.root,
                                    clock=lambda:self.now)
            second=restarted.execute(request)
        self.assertTrue(first["created"])
        self.assertTrue(second["replayed"])
        create.assert_called_once()

    def test_migrates_existing_queue_schema(self):
        old_db=self.root/"legacy.sqlite3"
        with closing(sqlite3.connect(old_db)) as db:
            db.execute("""CREATE TABLE spec_requests(
                request_id TEXT PRIMARY KEY, manifest_path TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,project_path TEXT NOT NULL,
                change_name TEXT NOT NULL,plan_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,created_at REAL NOT NULL,
                expires_at REAL NOT NULL,decision_at REAL,result_json TEXT
            )""")
            db.commit()
        ApprovalQueue(old_db,root=self.root)
        with closing(sqlite3.connect(old_db)) as db:
            fields={r[1] for r in db.execute("PRAGMA table_info(spec_requests)")}
        self.assertIn("running_at",fields)


if __name__=="__main__":
    unittest.main()

