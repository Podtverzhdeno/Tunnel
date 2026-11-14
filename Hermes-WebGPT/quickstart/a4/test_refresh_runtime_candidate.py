"""No-publication controls for the A4 local runtime candidate refresher.

These tests do not inspect user credentials, run Relay, start a browser,
publish any package, or modify the configured runtime.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quickstart.a4 import refresh_runtime_candidate as subject
from quickstart.a4.runtime_bundle import BundleError, _manifest_bytes


class RuntimeRefreshTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="a4-runtime-refresh-fixture-")
        self.addCleanup(tmp.cleanup)
        self.temp = Path(tmp.name)
        self.output = self.temp / "candidate.zip"
        self.manifest = {
            "files": [{"path": "relay.py", "size": 42, "sha256": "a"*64}],
        }
        self.manifest_sha = hashlib.sha256(_manifest_bytes(self.manifest)).hexdigest()
        self.patches = [
            patch.object(subject, "OUTPUT", self.output),
            patch.object(subject, "OLD_ARCHIVE", self.temp / "previous.zip"),
            patch.object(subject, "manifest_for",
                         return_value=(self.manifest, {"relay.py": b"fixture"})),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_readonly_report_does_not_create_archive(self):
        with patch.object(subject, "build_local_candidate",
                          side_effect=AssertionError("must not be called")):
            result = subject.refresh(build=False)
        self.assertEqual(result["status"], "READY_TO_BUILD")
        self.assertFalse(self.output.exists())
        self.assertEqual(result["files"], 1)
        self.assertTrue(result["local_only"])

    def test_never_overwrites_existing_candidate(self):
        self.output.write_bytes(b"existing-archive")
        with patch.object(subject, "build_local_candidate",
                          side_effect=AssertionError("must not be called")):
            with self.assertRaisesRegex(BundleError, "already_exists"):
                subject.refresh(build=True)
        self.assertEqual(self.output.read_bytes(), b"existing-archive")

    def test_mismatched_source_hash_does_not_claim_staged(self):
        with patch.object(subject, "build_local_candidate",
                          return_value={
                              "manifest_sha256": "f"*64,
                              "zip_sha256": "a"*64,
                              "files": 1,
                          }), patch.object(subject, "RuntimeStager",
                                           side_effect=AssertionError("no stage on drift")):
            with self.assertRaisesRegex(BundleError, "source_drift"):
                subject.refresh(build=True)

    def test_success_remains_candidate_not_installed(self):
        class Stager:
            def __init__(self, state):
                assert state.is_dir()
            def stage(self, *args):
                return {"state": "STAGED_NOT_ACTIVATED"}
            def verify(self, *args):
                return {"state": "VERIFIED_NOT_ACTIVATED"}
        with patch.object(subject, "build_local_candidate",
                          return_value={
                              "manifest_sha256": self.manifest_sha,
                              "zip_sha256": "f"*64,
                              "files": 1,
                          }), patch.object(subject, "RuntimeStager", Stager):
            result = subject.refresh(build=True)
        self.assertEqual(result["status"], "LOCAL_CANDIDATE_STAGED_VERIFIED")
        self.assertTrue(result["staged_isolated"])
        self.assertTrue(result["reverified_isolated"])
        self.assertFalse(result["published"])
        self.assertFalse(result["active_runtime_switched"])
        self.assertFalse(result["clean_clone_e2e_verified"])


if __name__ == "__main__":
    unittest.main()
