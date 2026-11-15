"""Actual A4 GUI release candidate build, manifest verification and no activation."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest

from quickstart.a4.build_gui_release import build, ROOT, SOURCE_FILES
from quickstart.a4.release_manager import ReleaseManager, ReleaseError


class GuiBundleBuildTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="a4-build-test-")
        self.addCleanup(temp.cleanup)
        self.temp=Path(temp.name)
        self.output=self.temp/"candidate"

    def test_build_actual_gui_and_verify_before_activation(self):
        item=build("v0.9.0-guiqa",self.output)
        self.assertTrue(item["ok"])
        self.assertFalse(item["activated"])
        for rel in SOURCE_FILES:
            self.assertEqual((self.output/rel).read_bytes(),(ROOT/rel).read_bytes())
        digest=hashlib.sha256((self.output/"manifest.json").read_bytes()).hexdigest()
        self.assertEqual(digest,item["manifest_sha256"])
        state=self.temp/"state";state.mkdir()
        manager=ReleaseManager(state)
        self.assertEqual(manager.preview(self.output,digest)["files_verified"],len(SOURCE_FILES))
        # The CMD in this bundle must never point at a missing PowerShell entry.
        self.assertIn("quickstart\\a4\\release_launch.ps1",
                      (self.output/"Setup-Hermes-A4.cmd").read_text(encoding="utf8"))
        self.assertTrue((self.output/"quickstart/a4/release_launch.ps1").is_file())
        self.assertIsNone(manager.active())
        self.assertFalse(manager.releases.exists())

    def test_builder_is_create_only_and_never_overwrites_existing_release(self):
        first=build("v0.9.0-guiqa",self.output)
        before=(self.output/"manifest.json").read_bytes()
        with self.assertRaisesRegex(ReleaseError,"destination_must_not_exist"):
            build("v0.9.1-guiqa",self.output)
        self.assertEqual((self.output/"manifest.json").read_bytes(),before)
        self.assertEqual(first["manifest_sha256"],hashlib.sha256(before).hexdigest())

    def test_invalid_version_refused_without_output(self):
        with self.assertRaisesRegex(ReleaseError,"invalid_release_version"):
            build("../.ssh","ignored")
        self.assertFalse(self.output.exists())


if __name__=="__main__":
    unittest.main()
