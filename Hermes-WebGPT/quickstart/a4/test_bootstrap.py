"""Pinned Hermes core installer checks without downloading or installing."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from quickstart.a4.bootstrap import (BootstrapError, EXPECTED_SHA256,
                                      plan, verified_installer, installer_path)


class BootstrapTests(unittest.TestCase):
    def test_pinned_script_and_origin_verified(self):
        details=plan()
        self.assertTrue(details["ok"])
        self.assertFalse(details["executed"])
        self.assertIn(EXPECTED_SHA256,details["sha256"])
        self.assertIn("ae3707b97e50e65c2031cff6b84ce5fc81024d11",
                      details["origin"])
        self.assertIn("-SkipBrowser",details["command_args"])
        self.assertTrue(Path(details["installer"]).is_file())

    def test_modified_or_missing_script_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            vendor=root / "vendor"
            vendor.mkdir()
            (vendor / "hermes-install.ps1").write_text("Write-Host compromised",
                                                        encoding="utf-8")
            (vendor / "LICENSE.NousResearch").write_text(
                "MIT License\n",encoding="utf-8")
            with self.assertRaisesRegex(BootstrapError,"hash_mismatch"):
                verified_installer(root)
            (vendor / "hermes-install.ps1").unlink()
            with self.assertRaisesRegex(BootstrapError,"missing"):
                verified_installer(root)

    def test_license_required(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            vendor=root / "vendor"
            vendor.mkdir()
            vendor_installer=vendor / "hermes-install.ps1"
            vendor_installer.write_bytes(installer_path().read_bytes())
            with self.assertRaisesRegex(BootstrapError,"license_missing"):
                verified_installer(root)

    @unittest.skipUnless(os.name=="nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell parser unavailable")
    def test_pinned_installer_parses_on_windows(self):
        path=str(installer_path()).replace("'","''")
        code=("$t=$null;$e=$null;"
              f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',"
              "[ref]$t,[ref]$e)|Out-Null;"
              "if($e.Count){$e | Out-String | Write-Error;exit 1}")
        result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive",
                               "-Command",code],
                              capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr[:1000])


if __name__=="__main__":
    unittest.main()
