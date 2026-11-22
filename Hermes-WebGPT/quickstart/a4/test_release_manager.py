"""Hermes A4 isolated offline updater integration tests; no production changes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from quickstart.a4.release_manager import ReleaseError, ReleaseManager


class ReleaseManagerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base=Path(t.name)
        self.state=self.base/"per-user-state"
        self.state.mkdir()
        self.bundle=self.base/"trusted-bundle"
        self.bundle.mkdir()
        self.manager=ReleaseManager(self.state)

    def bundle_for(self, version: str, *,
                   contents=b"verified-a4-setup", entries=None) -> str:
        files={
            "Setup-Hermes-A4.cmd": b"@echo off\n",
            "quickstart/a4/setup.ps1": contents,
        }
        if entries:
            files.update(entries)
        listing=[]
        for name, data in files.items():
            dest=self.bundle/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data)
            listing.append({"path":name,"sha256":hashlib.sha256(data).hexdigest()})
        manifest={"schema_version":1,"product":"Hermes-A4",
                  "version":version,"files":listing}
        raw=json.dumps(manifest,sort_keys=True).encode("utf8")
        (self.bundle/"manifest.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_status_does_not_create_release_directories(self):
        self.assertIsNone(self.manager.active())
        self.assertFalse((self.state/"a4-releases").exists())
        from quickstart.a4.release_manager import main
        self.assertEqual(main(["status","--state",str(self.state)]),0)
        self.assertFalse((self.state/"a4-releases").exists())

    def test_first_install_and_no_implicit_effect_on_workspace(self):
        sha=self.bundle_for("v1.0.0")
        out=self.manager.install(self.bundle,sha)
        self.assertEqual(out["state"],"activated")
        self.assertEqual(self.manager.active()["version"],"v1.0.0")
        staged=self.state/"a4-releases"/"v1.0.0"
        self.assertEqual((staged/"quickstart/a4/setup.ps1").read_bytes(),
                         b"verified-a4-setup")
        self.assertTrue((self.bundle/"quickstart/a4/setup.ps1").exists())
        self.assertFalse((self.state/"a4-update.lock").exists())
        self.assertEqual(self.manager.install(self.bundle,sha)["state"],"already_active")

    def test_version_switch_and_rollback_preserve_both_versions(self):
        first=self.bundle_for("v1.0.0")
        self.manager.install(self.bundle,first)
        second=self.bundle_for("v1.1.0",contents=b"new-a4-wizard")
        self.manager.install(self.bundle,second)
        self.assertEqual(self.manager.active()["version"],"v1.1.0")
        result=self.manager.rollback()
        self.assertEqual(result["version"],"v1.0.0")
        self.assertEqual(self.manager.active()["version"],"v1.0.0")
        self.assertTrue((self.state/"a4-active-release.rollback-current.json").exists())
        self.assertEqual((self.state/"a4-releases/v1.1.0/quickstart/a4/setup.ps1").read_bytes(),
                         b"new-a4-wizard")
        with self.assertRaises(ReleaseError):
            self.manager.rollback()

    def test_bad_manifest_hash_does_not_touch_active(self):
        sha=self.bundle_for("v1.0.0")
        with self.assertRaisesRegex(ReleaseError,"manifest_hash_mismatch"):
            self.manager.install(self.bundle,"0"*64)
        self.assertIsNone(self.manager.active())
        self.assertFalse((self.state/"a4-update.lock").exists())
        self.assertTrue((self.bundle/"manifest.json").exists())
        self.manager.install(self.bundle,sha)
        self.assertEqual(self.manager.active()["version"],"v1.0.0")

    def test_corrupted_file_fails_before_activation(self):
        sha=self.bundle_for("v1.0.0")
        (self.bundle/"quickstart/a4/setup.ps1").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ReleaseError,"bundle_file_hash_mismatch"):
            self.manager.install(self.bundle,sha)
        self.assertIsNone(self.manager.active())
        self.assertFalse((self.state/"a4-releases/v1.0.0").exists())

    def test_path_traversal_and_hidden_profile_files_rejected(self):
        for unsafe in ("../private.env", "profile/.env", "quickstart/a4/../bad.py",
                       "quickstart/a4/folder\\bad.ps1", "C:/Windows/System32/a.ps1"):
            # Construct only a malformed MANIFEST. Never create an unsafe
            # fixture path: on Windows an absolute path can escape our temp dir.
            self.bundle_for("v1.0.0")
            manifest_path=self.bundle/"manifest.json"
            doc=json.loads(manifest_path.read_text(encoding="utf8"))
            doc["files"].append({"path":unsafe,
                                 "sha256":hashlib.sha256(b"attack").hexdigest()})
            encoded=json.dumps(doc,sort_keys=True).encode("utf8")
            manifest_path.write_bytes(encoded)
            sha=hashlib.sha256(encoded).hexdigest()
            with self.assertRaises(ReleaseError):
                self.manager.install(self.bundle,sha)
            self.assertIsNone(self.manager.active())
            self.assertFalse((self.state/"a4-releases/v1.0.0").exists())

    def test_bad_release_version_denied(self):
        sha=self.bundle_for("../v1.0.0")
        with self.assertRaises(ReleaseError):
            self.manager.install(self.bundle,sha)

    def test_existing_busy_lock_is_not_overridden(self):
        sha=self.bundle_for("v1.0.0")
        (self.state/"a4-update.lock").write_text("other worker",encoding="utf8")
        with self.assertRaisesRegex(ReleaseError,"update_busy"):
            self.manager.install(self.bundle,sha)
        self.assertEqual((self.state/"a4-update.lock").read_text(encoding="utf8"),
                         "other worker")
        self.assertIsNone(self.manager.active())

    def test_current_pointer_symlink_denied(self):
        sha=self.bundle_for("v1.0.0")
        real=self.base/"elsewhere.json"
        real.write_text('{"version":"v1.0.0"}',encoding="utf8")
        try:
            (self.state/"a4-active-release.json").symlink_to(real)
        except (OSError,NotImplementedError):
            self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaisesRegex(ReleaseError,"unsafe_release_pointer"):
            self.manager.install(self.bundle,sha)
        self.assertEqual(real.read_text(encoding="utf8"),
                         '{"version":"v1.0.0"}')

    def test_unsigned_release_cannot_be_activated_by_cli_without_approval(self):
        from quickstart.a4.release_manager import main
        sha=self.bundle_for("v1.0.0")
        result=main(["install","--state",str(self.state),"--bundle",
                     str(self.bundle),"--manifest-sha256",sha])
        self.assertEqual(result,2)
        self.assertIsNone(self.manager.active())


if __name__=="__main__":
    unittest.main()
