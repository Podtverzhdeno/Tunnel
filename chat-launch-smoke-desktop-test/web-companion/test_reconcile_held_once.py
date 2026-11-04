"""Isolated unit tests for one-time held browser queue reconciliation."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import reconcile_held_once as m


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReconcileHeldOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "bridge-state"
        for folder in ("queue", "active", "done", "diagnostics"):
            (self.state / folder).mkdir(parents=True, exist_ok=True)
        self.hold = self.state / "admission-hold.json"
        self.archive = self.state / "owner-reconciliation" / "test-batch"
        self.hold_sha = write(self.hold, {"held": True})
        self.patch = [
            patch.object(m, "STATE", self.state),
            patch.object(m, "HOLD", self.hold),
            patch.object(m, "ARCHIVE", self.archive),
        ]
        for p in self.patch:
            p.start()
            self.addCleanup(p.stop)

    def test_fresh_zero_scan_and_reject_owned(self):
        now = datetime.now(timezone.utc).isoformat()
        write(self.state / "diagnostics" / "extension.json",
              {"at": now, "version": "0.6.5", "state": "polling"})
        path = self.state / "diagnostics" / "recovery-scan.json"
        good = {"at": now, "identified": 0, "eligible": 0, "recovered": 0,
                "ambiguous": 0, "stored_conflict": 0, "stored_owned": 4}
        write(path, good)
        self.assertEqual(m.fresh_zero_owned()["unresolved_browser_bindings"], 4)
        write(path, {**good, "identified": 1})
        with self.assertRaisesRegex(RuntimeError, "browser_owned_or_conflicting_tabs"):
            m.fresh_zero_owned()
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        write(path, {**good, "at": old})
        with self.assertRaisesRegex(RuntimeError, "browser_heartbeat_or_scan_not_fresh"):
            m.fresh_zero_owned()

    def test_queue_must_be_unsent(self):
        job = "123e4567-e89b-12d3-a456-426614174000"
        write(self.state / "queue" / (job + ".json"), {"id": job})
        write(self.state / "diagnostics" / (job + ".json"),
              [{"stage": "send_clicked"}])
        with self.assertRaisesRegex(RuntimeError, "queued_job_has_send_evidence"):
            m.checked_job("queue", job, "held_no_dispatch")

    def test_invalid_ack_rejected(self):
        job = "123e4567-e89b-12d3-a456-426614174001"
        write(self.state / "active" / (job + ".json"), {"id": job})
        with self.assertRaisesRegex(RuntimeError, "missing_or_unsafe_file"):
            m.checked_job("active", job, "manual_browser_tab_not_found",
                          require_ack=True)

    def test_apply_requires_foreground(self):
        with patch.object(m.sys, "stdin", SimpleNamespace(isatty=lambda: False)):
            with self.assertRaisesRegex(RuntimeError, "FOREGROUND_WINDOWS_TERMINAL_REQUIRED"):
                m.apply({}, "a" * 64)
        self.assertTrue(self.hold.exists())
        self.assertFalse(self.archive.exists())

    @unittest.skipUnless(os.name == "nt", "Windows owner-terminal semantics")
    def test_isolated_commit_quarantines_old_and_queues_ten(self):
        jobs = []
        for i in range(2):
            job = f"123e4567-e89b-12d3-a456-{i+100:012d}"
            state = "active" if i == 0 else "queue"
            path = self.state / state / (job + ".json")
            digest = write(path, {"id": job, "prompt": "synthetic-only"})
            jobs.append({"id": job, "source_state": state,
                         "classification": "synthetic", "job_sha256": digest})
        agents = [(f"backlog-{i:02d}-test", f"agent-briefs/backlog-{i:02d}-test.md")
                  for i in range(10)]
        doc = {"jobs": jobs, "hold_sha256": self.hold_sha,
               "new_dispatch_plan_sha256": "fake_sha",
               "new_roles": [x[0] for x in agents]}
        seq = {"value": 0}
        def fake_enqueue(role, brief):
            seq["value"] += 1
            jid = f"123e4567-e89b-12d3-a456-{seq['value']+300:012d}"
            write(self.state / "queue" / (jid + ".json"),
                  {"id": jid, "role": role, "prompt": "synthetic-only"})
            return {"queued": True, "job_id": jid}
        with patch.object(m.sys, "stdin", SimpleNamespace(isatty=lambda: True)), \
             patch("builtins.input", return_value="ARCHIVE " + "b"*16), \
             patch.object(m, "staged_agents", return_value=(agents, "fake_sha")), \
             patch.object(m, "fresh_zero_owned", return_value={}), \
             patch.object(m, "enqueue", side_effect=fake_enqueue):
            result = m.apply(doc, "b" * 64)
        self.assertEqual(result["archived"], 2)
        self.assertEqual(result["queued_new"], 10)
        self.assertTrue(result["hold_released"])
        self.assertEqual(result["done_claimed"], 0)
        self.assertEqual(len(list((self.state / "queue").glob("*.json"))), 10)
        self.assertFalse(list((self.state / "active").glob("*.json")))
        self.assertFalse(self.hold.exists())
        self.assertTrue((self.archive / "admission-hold.json").is_file())
        self.assertEqual(len(list((self.state / "done").glob("*.json"))), 0)
        self.assertEqual(json.loads((self.archive / "receipt.json").read_text())
                         ["new_jobs_staged"], 10)

    @unittest.skipUnless(os.name == "nt", "Windows owner-terminal semantics")
    def test_partial_enqueue_failure_rolls_back_with_hold(self):
        job = "123e4567-e89b-12d3-a456-426614174000"
        path = self.state / "active" / (job + ".json")
        digest = write(path, {"id": job, "prompt": "synthetic"})
        agents = [(f"backlog-{i:02d}-test", f"agent-briefs/backlog-{i:02d}-test.md")
                  for i in range(10)]
        doc = {"jobs": [{"id": job, "source_state": "active", "job_sha256": digest}],
               "hold_sha256": self.hold_sha,
               "new_dispatch_plan_sha256": "fake_sha",
               "new_roles": [x[0] for x in agents]}
        seq = {"value": 0}
        def bad_enqueue(role, brief):
            seq["value"] += 1
            if seq["value"] == 2:
                raise RuntimeError("synthetic_failure")
            jid = "123e4567-e89b-12d3-a456-426614174009"
            write(self.state / "queue" / (jid + ".json"),
                  {"id": jid, "prompt": "synthetic"})
            return {"queued": True, "job_id": jid}
        with patch.object(m.sys, "stdin", SimpleNamespace(isatty=lambda: True)), \
             patch("builtins.input", return_value="ARCHIVE " + "c"*16), \
             patch.object(m, "staged_agents", return_value=(agents, "fake_sha")), \
             patch.object(m, "fresh_zero_owned", return_value={}), \
             patch.object(m, "enqueue", side_effect=bad_enqueue):
            with self.assertRaisesRegex(RuntimeError, "synthetic_failure"):
                m.apply(doc, "c" * 64)
        self.assertTrue(self.hold.exists())
        self.assertTrue(path.exists())
        self.assertEqual(len(list((self.state / "queue").glob("*.json"))), 0)
        self.assertTrue((self.archive / "aborted-new").is_dir())
        self.assertEqual(len(list((self.state / "done").glob("*.json"))), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
