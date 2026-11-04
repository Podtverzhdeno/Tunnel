"""P085 protected acceptance finalizer: real SQLite + durable journal E2E.

All operations use temporary project files and test-only keys. The journal
implementation loaded in tests is canonical review_merge.py from checkout;
production MUST instead use an immutable, owner-pinned protected installation
copy of that module, NEVER live mutable source imported by the service.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from test_review_grant_broker_qa import ProtectedQaBrokerTests, QAKEY, POLICY
from workspace_mcp.review_grants import GrantDenied
from workspace_mcp.review_finalizer import ProtectedReviewFinalizer


def local_journal():
    """TEST-ONLY canonical current source, never production import."""
    source=Path(__file__).resolve().parents[2]/"review_merge.py"
    spec=importlib.util.spec_from_file_location("test_only_review_merge",source)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MergeIntent


class ProtectedFinalizerTests(unittest.TestCase):
    def setUp(self):
        self.q=ProtectedQaBrokerTests("test_joint_os_grant_qa_is_reservation_only")
        self.q.setUp()
        self.addCleanup(self.q.doCleanups)
        self.f=self.q.f
        with closing(sqlite3.connect(self.f.db)) as db, db:
            # Broker snapshot fixtures intentionally contain only the columns
            # needed for read-only verification. Add the canonical mutation
            # columns for this separate finalizer test suite.
            for col in ("review_notes","reviewed_at","activity_at"):
                db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
            db.execute("CREATE TABLE acceptance_records("
                       "task_id TEXT PRIMARY KEY,reviewer_role TEXT NOT NULL,"
                       "recorded_at TEXT NOT NULL,qa_json TEXT NOT NULL,"
                       "artifacts_json TEXT NOT NULL,spec_sha256 TEXT)")
            db.execute("CREATE TABLE events("
                       "id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,"
                       "role TEXT,action TEXT,detail TEXT,at TEXT)")
        self.journal=local_journal()
        self.finalizer=ProtectedReviewFinalizer(
            project=self.f.root,database=self.f.db,workspace="projects",
            owner_principals=self.f.policy.owner_principals,
            trusted_reviewer_actor=self.f.policy.reviewer_actor,
            ledger=self.f.ledger,qa_key=QAKEY,qa_policy=POLICY,
            journal_factory=self.journal)

    def state(self):
        with closing(sqlite3.connect(self.f.db)) as db:
            return db.execute("SELECT status,revision FROM tasks WHERE task_id='P152'").fetchone()

    def reservation(self,request):
        return self.q.reserve(request)

    def test_full_pinned_journal_accept_no_candidate_exec(self):
        request=self.q.request()
        reserved=self.reservation(request)
        merged=self.finalizer.accept_reserved(request,reserved)
        self.assertEqual(merged["status"],"done")
        self.assertEqual(set(merged["merged"]),{self.f.source,self.f.test_file})
        self.assertFalse(merged["retained_merge_intent"])
        self.assertEqual(self.state(),("done",8))
        self.assertEqual((self.f.root/self.f.source).read_bytes(),
                         (self.f.root/self.f.sandbox/self.f.source).read_bytes())
        with closing(sqlite3.connect(self.f.db)) as db:
            receipt=db.execute("SELECT qa_json,artifacts_json FROM acceptance_records "
                               "WHERE task_id='P152'").fetchone()
            self.assertEqual(len(json.loads(receipt[0])),6)
            self.assertEqual(len(json.loads(receipt[1])),2)
            self.assertEqual(db.execute("SELECT action FROM events").fetchone()[0],
                             "accept")
        self.assertFalse((self.f.root/".agent-team/merge-intents/P152").exists())

    def test_wrong_qa_grant_or_reservation_cannot_merge(self):
        request=self.q.request()
        for fake in ({}, {"state":"RESERVED_REQUIRES_PRIVILEGED_QA",
                          "task_id":"P152","formal_review_performed":False,
                          "source_merged":False}):
            with self.subTest(fake=fake),self.assertRaises(GrantDenied):
                self.finalizer.accept_reserved(request,fake)
        self.assertEqual(self.state(),("reviewing",7))
        self.assertFalse((self.f.root/self.f.source).exists())

    def test_changed_candidate_after_qa_cannot_merge(self):
        req=self.q.request()
        reservation=self.reservation(req)
        (self.f.root/self.f.sandbox/self.f.test_file).write_bytes(
            b"def test_foo(): assert False\n")
        with self.assertRaisesRegex(GrantDenied,"STALE_OR_CROSS_TASK_SCOPE"):
            self.finalizer.accept_reserved(req,reservation)
        self.assertEqual(self.state(),("reviewing",7))
        self.assertFalse((self.f.root/self.f.source).exists())

    def test_modified_spec_after_reservation_cannot_merge(self):
        req=self.q.request()
        reservation=self.reservation(req)
        (self.f.root/self.f.spec).write_bytes(b"changed spec\n")
        with self.assertRaisesRegex(GrantDenied,"SPEC_CHANGED_SINCE_ACQUIRE"):
            self.finalizer.accept_reserved(req,reservation)
        self.assertEqual(self.state(),("reviewing",7))

    def test_pending_legacy_merge_intent_blocks_finalization(self):
        req=self.q.request()
        reservation=self.reservation(req)
        (self.f.root/".agent-team/merge-intents/P152").mkdir(parents=True)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_STATE_NOT_SAFE"):
            self.finalizer.accept_reserved(req,reservation)
        self.assertEqual(self.state(),("reviewing",7))

    def test_double_commit_after_success_is_rejected(self):
        req=self.q.request()
        reservation=self.reservation(req)
        self.finalizer.accept_reserved(req,reservation)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_INVALID_AT_FINALIZATION"):
            self.finalizer.accept_reserved(req,reservation)
        with closing(sqlite3.connect(self.f.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM acceptance_records").fetchone()[0],1)

    def test_partial_merge_crash_leaves_journal_and_uncommitted_db(self):
        req=self.q.request()
        reservation=self.reservation(req)
        canonical=self.journal
        class CrashAfterOneFile(canonical):
            def apply(self):
                def injected(_name):
                    raise RuntimeError("injected-crash-after-first-file")
                super().apply(after_each=injected)
        crash_finalizer=ProtectedReviewFinalizer(
            project=self.f.root,database=self.f.db,workspace="projects",
            owner_principals=self.f.policy.owner_principals,
            trusted_reviewer_actor=self.f.policy.reviewer_actor,
            ledger=self.f.ledger,qa_key=QAKEY,qa_policy=POLICY,
            journal_factory=CrashAfterOneFile)
        with self.assertRaisesRegex(RuntimeError,"injected-crash"):
            crash_finalizer.accept_reserved(req,reservation)
        self.assertEqual(self.state(),("reviewing",7))
        self.assertTrue((self.f.root/".agent-team/merge-intents/P152").is_dir())
        with closing(sqlite3.connect(self.f.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM acceptance_records").fetchone()[0],0)
        diagnosis=self.journal(self.f.root,self.f.root/".agent-team","P152").inspect()
        self.assertTrue(any(entry["state"]=="applied" for entry in diagnosis["files"].values()))
        # Owner can reconcile this temp fixture after inspection; the code
        # under test does not automatically erase or reapply a crashed merge.
        self.assertFalse(diagnosis["diverged"])

    def test_signature_not_considered_done_before_staging(self):
        req=self.q.request()
        reservation=self.reservation(req)
        self.assertEqual(self.state(),("reviewing",7))
        with closing(sqlite3.connect(self.f.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0],0)

if __name__=="__main__":
    unittest.main()
