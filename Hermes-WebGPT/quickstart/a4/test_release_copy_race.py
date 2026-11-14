"""A4 update race regression: activation must follow verification of staged bytes.

Only disposable local temp directories and synthetic GUI files are used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from quickstart.a4.release_manager import ReleaseError, ReleaseManager


class ReleaseCopyRaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="a4-copy-race-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = self.root / "state"
        state.mkdir()
        self.mgr = ReleaseManager(state)

    def bundle(self, version: str):
        src = self.root / f"src-{version}"
        src.mkdir()
        values = {
            "Setup-Hermes-A4.cmd": b"@echo off\r\n",
            "quickstart/a4/setup.ps1": f"Write-Output '{version}'".encode("utf8"),
        }
        files = []
        for name, raw in values.items():
            target = src / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            files.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        manifest = {
            "schema_version": 1, "product": "Hermes-A4",
            "version": version, "files": files,
        }
        raw = json.dumps(manifest, sort_keys=True).encode("utf8")
        (src / "manifest.json").write_bytes(raw)
        return src, hashlib.sha256(raw).hexdigest()

    def sabotage_manifest_copy(self, src: Path):
        original = shutil.copyfile

        def changed_copy(source, target, *args, **kwargs):
            if Path(source) == src / "manifest.json":
                (src / "manifest.json").write_bytes(b"{}")
            return original(source, target, *args, **kwargs)

        return changed_copy

    def test_initial_install_refuses_changed_manifest_before_activation(self):
        source, digest = self.bundle("v1.0.0")
        with patch("quickstart.a4.release_manager.shutil.copyfile",
                   side_effect=self.sabotage_manifest_copy(source)):
            with self.assertRaisesRegex(ReleaseError, "manifest|integrity"):
                self.mgr.install(source, digest)
        self.assertIsNone(self.mgr.active())
        self.assertFalse((self.mgr.releases / "v1.0.0").exists())
        self.assertFalse(self.mgr.lock.exists())

    def test_failed_update_keeps_last_verified_active_release(self):
        old, oldpin = self.bundle("v1.0.0")
        self.mgr.install(old, oldpin)
        new, newpin = self.bundle("v1.1.0")
        with patch("quickstart.a4.release_manager.shutil.copyfile",
                   side_effect=self.sabotage_manifest_copy(new)):
            with self.assertRaisesRegex(ReleaseError, "manifest|integrity"):
                self.mgr.install(new, newpin)
        self.assertEqual(self.mgr.active()["version"], "v1.0.0")
        self.assertFalse((self.mgr.releases / "v1.1.0").exists())
        self.assertFalse(self.mgr.previous.exists())
        self.assertFalse(self.mgr.lock.exists())


if __name__ == "__main__":
    unittest.main()
