"""Static, cross-platform safety gates; not a Windows browser E2E."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
LAUNCH = (ROOT / "Start-Hermes-Edge-Temporary.ps1").read_text(encoding="utf8")
METRIC = (ROOT / "Measure-Hermes-Edge-CPU.ps1").read_text(encoding="utf8")


class EdgeTemporaryLauncherContract(unittest.TestCase):
    def test_temporary_url_exact(self):
        self.assertIn("https://chatgpt.com/?temporary-chat=true", LAUNCH)
        self.assertNotIn("https://chatgpt.com/#", LAUNCH)

    def test_explicit_isolated_profile(self):
        self.assertIn("EdgeTemporaryProfile", LAUNCH)
        self.assertIn("--user-data-dir=", LAUNCH)
        self.assertIn("already_running", LAUNCH)
        self.assertIn("Get-CimInstance", LAUNCH)

    def test_does_not_stop_browser_or_disable_security(self):
        for forbidden in (
            "Stop-Process", "taskkill", "--no-sandbox",
            "--disable-web-security", "--disable-site-isolation-trials",
            "--disable-gpu", "--disable-extensions",
        ):
            self.assertNotIn(forbidden, LAUNCH)
        self.assertIn("Start-Process", LAUNCH)

    def test_debugger_only_when_opted_in(self):
        self.assertIn("$DebugPort=0", LAUNCH)
        self.assertIn("if($DebugPort -ne 0)", LAUNCH)
        self.assertIn("--remote-debugging-address=127.0.0.1", LAUNCH)

    def test_no_false_temporary_mode_confirmation(self):
        self.assertIn("temporary_ui_verified=$false", LAUNCH)
        self.assertIn("browser_survival_verified=$false", LAUNCH)

    def test_metrics_read_only_and_bounded(self):
        self.assertIn("ValidateRange(5,300)", METRIC)
        self.assertIn("Get-Process", METRIC)
        for forbidden in ("Stop-Process", "taskkill", "Set-ItemProperty",
                          "Remove-Item", "reg add", "Start-Process"):
            self.assertNotIn(forbidden, METRIC)
        self.assertIn("cpu_percent_of_machine", METRIC)


if __name__ == "__main__":
    unittest.main()
