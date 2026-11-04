"""P085 trust-input snapshot regressions: disposable project and SQLite only."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workspace_mcp.review_target_snapshot import snapshot
from workspace_mcp.review_grants import GrantDenied

NOW = datetime(2026,11,2,17,0,tzinfo=timezone.utc)
PRINCIPALS = {f"developer-{i}":f"dev:actor{i}" for i in range(1,5)}

def sha(data):
    return hashlib.sha256(data).hexdigest()


class HostSnapshotTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="p085-offline-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.db=self.root/"queue.sqlite3"
        self.spec="openspec/changes/demo/specs/review/spec.md"
        self.sandbox=".agent-team/workspaces/developer-1/P152"
        self.prod="candidate_module.py"
        self.test="test_candidate_module.py"
        self.files={self.prod:b"def f(): return 1\n",self.test:b"def test(): assert True\n"}
        for path,data in [(self.spec,b"spec source\n"),
                          *((self.sandbox+"/"+k,v) for k,v in self.files.items())]:
            p=self.root/path
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(data)
        self.baseline={"__spec_sha__":sha((self.root/self.spec).read_bytes()),
                       self.prod:None,self.test:None}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE tasks("
                       "task_id TEXT PRIMARY KEY,owner TEXT,status TEXT,review_token TEXT,"
                       "review_until TEXT,revision INTEGER,sandbox TEXT,deliverables TEXT,"
                       "baseline TEXT,spec_ref TEXT)")
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("P152","developer-1","reviewing","r"*30,
                        (NOW+timedelta(minutes=30)).isoformat(),4,self.sandbox,
                        json.dumps([self.test,self.prod]),json.dumps(self.baseline),
                        self.spec+" / Review requirement"))
        self.args={"workspace":"projects","owner_principals":PRINCIPALS,"now":NOW}

    def run_snapshot(self):
        return snapshot(self.root,self.db,"P152",**self.args)

    def update(self,**values):
        allowed={"owner","status","review_token","review_until","revision",
                 "sandbox","deliverables","baseline","spec_ref"}
        self.assertFalse(set(values)-allowed)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE tasks SET "+",".join(f"{k}=?" for k in values)+
                       " WHERE task_id='P152'",tuple(values.values()))

    def test_snapshot_exact_file_bundle_and_author(self):
        target=self.run_snapshot()
        self.assertEqual(target.workspace,"projects")
        self.assertEqual(target.task_id,"P152")
        self.assertEqual(target.revision,4)
        self.assertEqual(target.author_actor_id,PRINCIPALS["developer-1"])
        self.assertTrue(target.review_lease_active)
        self.assertFalse(target.merge_intent_pending)
        self.assertEqual(len(target.source_bundle_sha256),64)
        self.assertFalse((self.root/"queue.sqlite3-wal").exists())

    def test_reordered_deliverable_metadata_is_same_bundle(self):
        before=self.run_snapshot().source_bundle_sha256
        self.update(deliverables=json.dumps([self.prod,self.test]))
        self.assertEqual(before,self.run_snapshot().source_bundle_sha256)

    def test_review_lease_rotation_changes_scope_even_without_source_change(self):
        old=self.run_snapshot().review_lease_sha256
        self.update(review_token="s"*30)
        fresh=self.run_snapshot().review_lease_sha256
        self.assertNotEqual(old,fresh)
        self.assertEqual(len(fresh),64)

    def test_candidate_source_drift_changes_signed_scope(self):
        before=self.run_snapshot().source_bundle_sha256
        (self.root/self.sandbox/self.prod).write_bytes(b"def f(): return 5\n")
        after=self.run_snapshot().source_bundle_sha256
        self.assertNotEqual(before,after)

    def test_candidate_test_drift_changes_signed_scope(self):
        before=self.run_snapshot().source_bundle_sha256
        (self.root/self.sandbox/self.test).write_bytes(b"def test(): assert False\n")
        self.assertNotEqual(before,self.run_snapshot().source_bundle_sha256)

    def test_spec_drift_denied(self):
        (self.root/self.spec).write_bytes(b"new spec\n")
        with self.assertRaisesRegex(GrantDenied,"SPEC_CHANGED_SINCE_ACQUIRE"):
            self.run_snapshot()

    def test_main_conflict_denied(self):
        (self.root/self.prod).write_bytes(b"main changed\n")
        with self.assertRaisesRegex(GrantDenied,"MAIN_BASELINE_CHANGED"):
            self.run_snapshot()

    def test_expired_and_invalid_lease_denied(self):
        self.update(review_until=(NOW-timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_NOT_ACTIVE"):
            self.run_snapshot()
        self.update(review_until=(NOW+timedelta(minutes=5)).isoformat(),
                    review_token=None)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_NOT_ACTIVE"):
            self.run_snapshot()

    def test_nonreview_and_missing_task_denied(self):
        self.update(status="review")
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_NOT_ACTIVE"):
            self.run_snapshot()
        with self.assertRaisesRegex(GrantDenied,"TASK_NOT_FOUND"):
            snapshot(self.root,self.db,"P153",**self.args)

    def test_malformed_revision_denied(self):
        self.update(revision=-1)
        with self.assertRaisesRegex(GrantDenied,"REVIEW_LEASE_NOT_ACTIVE"):
            self.run_snapshot()

    def test_untrusted_sandbox_path_denied(self):
        self.update(sandbox="../other/P152")
        with self.assertRaisesRegex(GrantDenied,"SANDBOX_SCOPE_MISMATCH"):
            self.run_snapshot()

    def test_merge_pending_detected_by_folder_even_without_manifest(self):
        p=self.root/".agent-team/merge-intents/P152"
        p.mkdir(parents=True)
        self.assertTrue(self.run_snapshot().merge_intent_pending)

    def test_review_file_missing_and_unlisted_file(self):
        (self.root/self.sandbox/self.test).unlink()
        with self.assertRaises(GrantDenied):
            self.run_snapshot()

    def test_reject_linked_candidate_if_supported(self):
        p=self.root/self.sandbox/self.prod
        q=self.root/"link_to_prod"
        try:
            q.symlink_to(p)
        except (OSError,NotImplementedError):
            self.skipTest("OS disallows symlinks")
        p.unlink()
        p.symlink_to(q)
        with self.assertRaisesRegex(GrantDenied,"LINKED_REVIEW_PATH"):
            self.run_snapshot()

    def test_untrusted_role_map_denied(self):
        bad={**PRINCIPALS,"architect-reviewer":"dev:actorX"}
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_AUTHOR_MAPPING_MISSING"):
            snapshot(self.root,self.db,"P152",workspace="projects",
                     owner_principals=bad,now=NOW)

    def test_no_mutation_of_coordinator(self):
        before=self.db.read_bytes()
        self.run_snapshot()
        self.run_snapshot()
        self.assertEqual(before,self.db.read_bytes())

    def test_spec_path_escape_denied(self):
        self.update(spec_ref="openspec/../secret.txt / suspicious")
        with self.assertRaisesRegex(GrantDenied,"UNSAFE_REVIEW_PATH"):
            self.run_snapshot()


if __name__=="__main__":
    unittest.main()
