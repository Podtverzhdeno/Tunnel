"""Tests for safe offline source staging without executing user code."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from quickstart.a4.dependency_closure import REQUIRED_PY_FILES
from quickstart.a4.runtime_bundle import build_local_candidate
from quickstart.a4.runtime_stage import RuntimeStager, StageError, main


class RuntimeStageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.source=root/"source"
        self.state=root/"state"
        self.archive=root/"runtime.zip"
        self.source.mkdir()
        self.state.mkdir()
        for name in REQUIRED_PY_FILES:
            (self.source/name).write_text("TEST = 'synthetic source'\n",encoding="utf8")
        self.pins=build_local_candidate(self.source,self.archive,"v2.1.0",
                                        explicit_local_only=True)
        self.manager=RuntimeStager(self.state)

    def stage(self):
        return self.manager.stage(self.archive,"v2.1.0",
                                  self.pins["manifest_sha256"],
                                  self.pins["zip_sha256"])

    def test_stages_and_verifies_without_activating_process(self):
        response=self.stage()
        self.assertEqual(response["state"],"STAGED_NOT_ACTIVATED")
        self.assertFalse(response["remote_delivery_verified"])
        self.assertFalse((self.state/"a4-runtime-selected.json").exists())
        status=self.manager.verify("v2.1.0",self.pins["manifest_sha256"])
        self.assertEqual(status["files_verified"],len(REQUIRED_PY_FILES))
        self.assertTrue((self.state/"a4-runtime-versions/v2.1.0/relay.py").exists())
        self.assertFalse((self.state/"a4-runtime-stage.lock").exists())

    def test_tampered_archive_hash_fails_before_state_changes(self):
        with self.archive.open("ab") as out:
            out.write(b"tamper")
        with self.assertRaisesRegex(StageError,"archive_hash_mismatch"):
            self.stage()
        self.assertFalse((self.state/"a4-runtime-versions").exists())

    def test_wrong_manifest_pin_denied(self):
        with self.assertRaisesRegex(StageError,"candidate_validation_failed"):
            self.manager.stage(self.archive,"v2.1.0","0"*64,
                               self.pins["zip_sha256"])
        self.assertFalse((self.state/"a4-runtime-versions/v2.1.0").exists())

    def test_staged_payload_mutation_is_detected(self):
        self.stage()
        module=self.state/"a4-runtime-versions/v2.1.0/relay.py"
        module.write_text("changed = True\n",encoding="utf8")
        with self.assertRaisesRegex(StageError,"file_changed"):
            self.manager.verify("v2.1.0",self.pins["manifest_sha256"])

    def test_missing_staged_file_is_detected(self):
        self.stage()
        (self.state/"a4-runtime-versions/v2.1.0/relay.py").unlink()
        with self.assertRaisesRegex(StageError,"inventory_changed"):
            self.manager.verify("v2.1.0",self.pins["manifest_sha256"])

    def test_repeated_stage_is_refused_without_overwrite(self):
        self.stage()
        with self.assertRaisesRegex(StageError,"already_staged"):
            self.stage()
        self.assertEqual(self.manager.verify(
            "v2.1.0",self.pins["manifest_sha256"])["files_verified"],
            len(REQUIRED_PY_FILES))

    def test_active_lock_is_preserved(self):
        lock=self.state/"a4-runtime-stage.lock"
        lock.write_text("another session",encoding="utf8")
        with self.assertRaisesRegex(StageError,"stage_busy"):
            self.stage()
        self.assertEqual(lock.read_text(encoding="utf8"),"another session")

    def test_cli_requires_explicit_approval(self):
        code=main(["stage","--state",str(self.state),
                   "--archive",str(self.archive),"--version","v2.1.0",
                   "--manifest-sha256",self.pins["manifest_sha256"],
                   "--archive-sha256",self.pins["zip_sha256"]])
        self.assertEqual(code,2)
        self.assertFalse((self.state/"a4-runtime-versions").exists())

    def test_wrong_version_is_rejected_even_with_valid_bundle(self):
        with self.assertRaisesRegex(StageError,"version_mismatch"):
            self.manager.stage(self.archive,"v2.2.0",
                               self.pins["manifest_sha256"],
                               self.pins["zip_sha256"])

    def test_unsafe_state_symlink_denied_when_supported(self):
        another=self.state.parent/"real-state"
        another.mkdir()
        linked=self.state.parent/"linked-state"
        try: linked.symlink_to(another,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        with self.assertRaisesRegex(StageError,"invalid_runtime_state"):
            RuntimeStager(linked)

if __name__=="__main__":
    unittest.main()
