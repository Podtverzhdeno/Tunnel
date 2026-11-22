"""A4 GUI and double-click launcher static/syntax tests (never opens UI)."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SetupTests(unittest.TestCase):
    def test_entrypoints_present(self):
        cmd = ROOT / "Setup-Hermes-A4.cmd"
        gui = ROOT / "quickstart" / "a4" / "setup.ps1"
        self.assertTrue(cmd.is_file())
        self.assertTrue(gui.is_file())
        text = cmd.read_text(encoding="utf-8")
        self.assertIn("-STA", text)
        self.assertIn("quickstart\\a4\\setup.ps1", text)

    def test_gui_no_unguarded_launch(self):
        gui = (ROOT / "quickstart" / "a4" / "setup.ps1").read_text(encoding="utf-8")
        self.assertIn("pending_backend_integration", gui)
        self.assertIn("Export-Clixml", gui)
        self.assertIn("control-plane-key", gui)
        self.assertIn("read_only", gui)
        self.assertIn("full_access", gui)
        self.assertNotIn("Start-Hermes-All.cmd", gui)
        self.assertNotIn("start.ps1 -File", gui)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows WinForms smoke requires PowerShell")
    def test_real_gui_construction_without_user_interaction(self):
        f = ROOT / "quickstart" / "a4" / "setup.ps1"
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(f), "-SmokeTest"],
            capture_output=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"A4_GUI_SMOKE_OK", result.stdout)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "PowerShell 5.1 is required")
    def test_powershell_parser_accepts_gui(self):
        f = ROOT / "quickstart" / "a4" / "setup.ps1"
        literal = str(f).replace("'", "''")
        command = (
            "$tokens=$null; $errors=$null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{literal}',"
            "[ref]$tokens,[ref]$errors) | Out-Null; "
            "if($errors.Count -gt 0) { $errors | Out-String | Write-Error; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", command], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
