"""Real Windows A4 stable launch selection: genuine PowerShell, temporary state.

No browser/network/Telegram/process restart; staged scripts are synthetic,
signed by test-generated SHA256 manifest, and run only with -SmokeTest.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from quickstart.a4.release_manager import ReleaseManager

ROOT=Path(__file__).resolve().parents[2]
LAUNCH=ROOT/"quickstart/a4/release_launch.ps1"


@unittest.skipUnless(os.name=="nt","Windows PowerShell 5.1 is required")
class ReleaseLauncherAcceptance(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="hermes-a4-release-launch-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.state=self.root/"Hermes-OneClick"
        self.state.mkdir()
        self.bundle=self.root/"bundle"
        self.bundle.mkdir()
        self.manager=ReleaseManager(self.state)
        self.env=os.environ.copy()
        self.env["LOCALAPPDATA"]=str(self.root)

    def bundle_for(self, version: str, *, text="A4_RELEASE_SMOKE_V1"):
        files={
           "Setup-Hermes-A4.cmd":b"@echo off\r\n",
           "quickstart/a4/setup.ps1": (
              "param([switch]$SmokeTest)\n"
              f"if($SmokeTest){{Write-Output '{text}'}} else {{exit 5}}\n"
           ).encode("utf8")
        }
        listing=[]
        for rel,raw in files.items():
            path=self.bundle/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
            listing.append({"path":rel,"sha256":hashlib.sha256(raw).hexdigest()})
        manifest={"schema_version":1,"product":"Hermes-A4",
                  "version":version,"files":listing}
        raw=json.dumps(manifest,sort_keys=True).encode("utf8")
        (self.bundle/"manifest.json").write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        self.manager.install(self.bundle,digest)
        return digest

    def launch(self, switch):
        return subprocess.run(
           ["powershell.exe","-NoLogo","-NoProfile","-NonInteractive",
            "-ExecutionPolicy","Bypass","-File",str(LAUNCH),switch],
           cwd=ROOT,env=self.env,capture_output=True,text=True,
           encoding="utf8",errors="replace",timeout=30)

    def test_source_fallback_when_no_version_selected(self):
        selected=self.launch("-ResolveOnly")
        self.assertEqual(selected.returncode,0,selected.stderr)
        payload=json.loads(selected.stdout.strip())
        self.assertFalse(payload["release_active"])
        self.assertEqual(Path(payload["selected"]).resolve(),
                         ROOT/"quickstart/a4/setup.ps1")

    def test_real_source_gui_smoke_through_stable_launcher(self):
        run=self.launch("-SmokeTest")
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("A4_GUI_SMOKE_OK",run.stdout)

    def test_real_staged_gui_smoke_through_stable_launcher(self):
        # Package the actual A4 GUI source, not an inert synthetic marker.
        self.bundle_for("v1.0.0")
        actual=(ROOT/"quickstart/a4/setup.ps1").read_bytes()
        staged=self.bundle/"quickstart/a4/setup.ps1"
        staged.write_bytes(actual)
        doc=json.loads((self.bundle/"manifest.json").read_text())
        for entry in doc["files"]:
            if entry["path"]=="quickstart/a4/setup.ps1":
                entry["sha256"]=hashlib.sha256(actual).hexdigest()
        raw=json.dumps(doc,sort_keys=True).encode()
        (self.bundle/"manifest.json").write_bytes(raw)
        # The first candidate is already active, so use a second version
        # with the same verified bytes to exercise a real GUI release.
        doc["version"]="v1.1.0"
        raw=json.dumps(doc,sort_keys=True).encode()
        (self.bundle/"manifest.json").write_bytes(raw)
        self.manager.install(self.bundle,hashlib.sha256(raw).hexdigest())
        run=self.launch("-SmokeTest")
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("A4_GUI_SMOKE_OK",run.stdout)

    def test_versioned_gui_is_really_selected_and_executed(self):
        self.bundle_for("v1.0.0")
        selected=self.launch("-ResolveOnly")
        self.assertEqual(selected.returncode,0,selected.stderr)
        payload=json.loads(selected.stdout.strip())
        self.assertTrue(payload["release_active"])
        self.assertEqual(Path(payload["selected"]).resolve(),
                         self.state/"a4-releases/v1.0.0/quickstart/a4/setup.ps1")
        run=self.launch("-SmokeTest")
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("A4_RELEASE_SMOKE_V1",run.stdout)

    def test_rollback_launches_previous_version(self):
        self.bundle_for("v1.0.0",text="A4_RELEASE_SMOKE_V1")
        self.bundle_for("v1.1.0",text="A4_RELEASE_SMOKE_V2")
        self.assertIn("A4_RELEASE_SMOKE_V2",self.launch("-SmokeTest").stdout)
        self.manager.rollback()
        restored=self.launch("-SmokeTest")
        self.assertEqual(restored.returncode,0,restored.stderr)
        self.assertIn("A4_RELEASE_SMOKE_V1",restored.stdout)
        self.assertNotIn("A4_RELEASE_SMOKE_V2",restored.stdout)

    def test_tamper_blocks_gui_without_source_fallback(self):
        self.bundle_for("v1.0.0")
        (self.state/"a4-releases/v1.0.0/quickstart/a4/setup.ps1").write_bytes(b"bad")
        result=self.launch("-SmokeTest")
        self.assertEqual(result.returncode,2,result.stdout)
        self.assertNotIn("A4_GUI_SMOKE_OK",result.stdout)
        self.assertNotIn("A4_RELEASE_SMOKE_V1",result.stdout)

    def test_missing_pointer_target_blocks_gui(self):
        self.bundle_for("v1.0.0")
        (self.state/"a4-releases/v1.0.0/quickstart/a4/setup.ps1").unlink()
        result=self.launch("-SmokeTest")
        self.assertEqual(result.returncode,2,result.stdout)
        self.assertNotIn("A4_GUI_SMOKE_OK",result.stdout)


if __name__=="__main__":
    unittest.main()
