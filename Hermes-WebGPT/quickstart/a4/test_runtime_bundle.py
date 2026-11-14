"""Security regression tests for A4 offline runtime source candidate bundles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from quickstart.a4.dependency_closure import REQUIRED_PY_FILES
from quickstart.a4.runtime_bundle import (
    BundleError, build_local_candidate, manifest_for, verify_candidate,
)


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "checkout"
        self.source.mkdir()
        self.out = self.root / "private-runtime-candidate.zip"
        for i, name in enumerate(REQUIRED_PY_FILES):
            (self.source/name).write_text(f"VALUE = {i}\n", encoding="utf8")

    def test_valid_local_candidate_without_public_release_claim(self):
        result = build_local_candidate(
            self.source, self.out, "v1.0.0",
            explicit_local_only=True)
        self.assertEqual(result["state"], "LOCAL_CANDIDATE_NOT_PUBLISHED")
        self.assertEqual(result["files"], len(REQUIRED_PY_FILES))
        check = verify_candidate(self.out, result["manifest_sha256"])
        self.assertTrue(check["ok"])
        self.assertFalse(check["approved_for_public_release"])
        with zipfile.ZipFile(self.out) as z:
            self.assertEqual(
                set(z.namelist()),
                set(REQUIRED_PY_FILES) | {"manifest.json"})
            self.assertEqual(
                z.read("gateway_runner.py"),
                (self.source/"gateway_runner.py").read_bytes())

    def test_requires_explicit_local_only_flag(self):
        with self.assertRaisesRegex(BundleError, "explicit_local"):
            build_local_candidate(self.source, self.out, "v1.0.0")
        self.assertFalse(self.out.exists())

    def test_missing_module_does_not_leave_artifact(self):
        (self.source/"relay.py").unlink()
        with self.assertRaisesRegex(BundleError, "module_missing"):
            build_local_candidate(self.source, self.out, "v1.0.0",
                                  explicit_local_only=True)
        self.assertFalse(self.out.exists())

    def test_possible_secret_blocks_even_local_bundle(self):
        (self.source/"relay.py").write_text(
            "TOKEN = 'sk-" + "A"*35 + "'\n", encoding="utf8")
        with self.assertRaisesRegex(BundleError,"secret"):
            build_local_candidate(self.source, self.out, "v1.0.0",
                                  explicit_local_only=True)
        self.assertFalse(self.out.exists())

    def test_non_python_source_denied(self):
        (self.source/"gateway_runner.py").write_text(
            "def invalid(:\n", encoding="utf8")
        with self.assertRaisesRegex(BundleError, "invalid_python"):
            manifest_for(self.source, "v1.0.0")

    def test_bundle_inside_checkout_denied(self):
        with self.assertRaisesRegex(BundleError,"source_root"):
            build_local_candidate(self.source,self.source/"candidate.zip",
                                  "v1.0.0",explicit_local_only=True)

    def test_existing_bundle_never_overwritten(self):
        self.out.write_bytes(b"valuable review artifact")
        with self.assertRaisesRegex(BundleError,"output_exists"):
            build_local_candidate(self.source,self.out,"v1.0.0",
                                  explicit_local_only=True)
        self.assertEqual(self.out.read_bytes(), b"valuable review artifact")

    def test_bad_external_manifest_checksum_is_rejected(self):
        result=build_local_candidate(self.source,self.out,"v1.0.0",
                                     explicit_local_only=True)
        with self.assertRaisesRegex(BundleError,"manifest_hash_mismatch"):
            verify_candidate(self.out, "0"*64)
        self.assertEqual(len(result["zip_sha256"]),64)

    def test_extra_zip_entry_and_missing_module_fail(self):
        result=build_local_candidate(self.source,self.out,"v1.0.0",
                                     explicit_local_only=True)
        with zipfile.ZipFile(self.out,"a") as z:
            z.writestr(".env","SYNTHETIC_PRIVATE_DATA=not-a-real-secret")
        with self.assertRaisesRegex(BundleError,"inventory_mismatch"):
            verify_candidate(self.out,result["manifest_sha256"])

    def test_same_source_gives_same_bytes(self):
        first=build_local_candidate(self.source,self.out,"v1.0.0",
                                    explicit_local_only=True)
        other=self.root/"other.zip"
        second=build_local_candidate(self.source,other,"v1.0.0",
                                     explicit_local_only=True)
        self.assertEqual(first["zip_sha256"],second["zip_sha256"])

    def test_symlinked_source_denied_when_supported(self):
        p=self.source/"relay.py"
        p.unlink()
        try:
            p.symlink_to(self.source/"gateway_runner.py")
        except (OSError,NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        with self.assertRaisesRegex(BundleError,"module_missing"):
            manifest_for(self.source,"v1.0.0")


if __name__=="__main__":
    unittest.main()
