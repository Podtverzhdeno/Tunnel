"""Offline A4 secret-store integration tests: synthetic tokens only."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quickstart import services


class SecretBootstrapTests(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        (self.root / "profile").mkdir()

    def test_gateway_receives_env_token_without_plaintext_env_file(self):
        with patch.object(services, "ROOT", self.root), \
             patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake-test-token"}, clear=True):
            vars_ = services._gateway_env_available()
        self.assertEqual(vars_["TELEGRAM_BOT_TOKEN"], "fake-test-token")
        self.assertFalse((self.root / "profile" / ".env").exists())

    def test_existing_profile_token_takes_precedence(self):
        (self.root / "profile" / ".env").write_text(
            "TELEGRAM_BOT_TOKEN=profile-test-token\n", encoding="utf-8")
        with patch.object(services, "ROOT", self.root), \
             patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fallback-test-token"}, clear=True):
            vars_ = services._gateway_env_available()
        self.assertEqual(vars_["TELEGRAM_BOT_TOKEN"], "profile-test-token")

    def test_absent_key_denied(self):
        with patch.object(services, "ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(services.StartupBlocked) as err:
                services._gateway_env_available()
        self.assertEqual(str(err.exception), "telegram_bot_token_missing")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_real_windows_dpapi_roundtrip_with_synthetic_key(self):
        import subprocess
        with TemporaryDirectory() as td:
            path = Path(td) / "synthetic.dpapi.xml"
            dest = str(path).replace("'", "''")
            command = (
                "$key = ConvertTo-SecureString 'synthetic-test-token' -AsPlainText -Force; "
                f"$key | Export-Clixml -LiteralPath '{dest}'; "
                f"$restore = Import-Clixml -LiteralPath '{dest}'; "
                "$plain = [System.Net.NetworkCredential]::new('', $restore).Password; "
                "if ($plain -eq 'synthetic-test-token') { Write-Output 'OK' } "
                "else { exit 7 }"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=30
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "OK")
            self.assertNotIn(b"synthetic-test-token", path.read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell required")
    def test_legacy_launcher_fails_closed_after_a4_choice(self):
        import subprocess
        with TemporaryDirectory() as td:
            state = Path(td) / "Hermes-OneClick"
            state.mkdir()
            (state / "settings-a4.json").write_text(
                '{"schema_version":1,"mode":"read_only"}', encoding="utf-8")
            env = os.environ.copy()
            env["LOCALAPPDATA"] = td
            env.pop("TELEGRAM_BOT_TOKEN", None)
            script = Path(__file__).resolve().parents[2] / "quickstart" / "start.ps1"
            proc = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(script)],
                env=env, capture_output=True, timeout=35,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn(b"A4", proc.stdout)

    def test_powershell_launcher_consumes_matching_dpapi_filename(self):
        script = (Path(__file__).resolve().parents[2] / "quickstart" / "start.ps1").read_text(encoding="utf-8-sig")
        gui = (Path(__file__).resolve().parent / "setup.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Secret $telegram.Text', gui)
        self.assertIn('Secret $api.Text', gui)
        self.assertIn("telegram-token.dpapi.xml", script)
        self.assertIn("Import-Clixml", script)
        self.assertIn("$TelegramInjectedByLauncher", script)
        self.assertIn("Remove-Item Env:TELEGRAM_BOT_TOKEN", script)


if __name__ == "__main__":
    unittest.main()
