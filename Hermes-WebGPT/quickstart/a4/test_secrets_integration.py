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

    def test_gateway_process_environment_strips_unrelated_privileged_keys(self):
        ambient={
            "TELEGRAM_BOT_TOKEN": "ambient-telegram",
            "CONTROL_PLANE_API_KEY": "synthetic-cp-ambient",
            "LOCAL_MCP_OBSERVER_BEARER": "synthetic-observer",
            "HERMES_A4_OBSERVER_AUTHORIZATION": "Bearer synthetic",
            "MCP_EXTRA_HEADERS": "Authorization: env:SYNTHETIC",
            "MCP_DISCOVERY_EXTRA_HEADERS": "Authorization: env:SYNTHETIC",
            "UNRELATED_ALLOWED": "retained",
        }
        profile={
            "TELEGRAM_BOT_TOKEN": "configured-telegram",
            "CONTROL_PLANE_API_KEY": "synthetic-cp-profile",
            "control_plane_api_key": "mixed-case-must-be-stripped",
            "Mcp_Extra_Headers": "mixed-case-must-be-stripped",
            "MCP_EXTRA_HEADERS": "must-be-stripped",
            "CUSTOM_GATEWAY_VALUE": "kept",
        }
        with patch.object(services, "ROOT", self.root):
            child=services._gateway_child_environment(profile,ambient)
        self.assertEqual(child["TELEGRAM_BOT_TOKEN"],"configured-telegram")
        self.assertEqual(child["CUSTOM_GATEWAY_VALUE"],"kept")
        self.assertEqual(child["UNRELATED_ALLOWED"],"retained")
        for key in services._GATEWAY_ENV_DENY:
            self.assertNotIn(key,child)
        self.assertNotIn("control_plane_api_key",child)
        self.assertNotIn("Mcp_Extra_Headers",child)
        self.assertNotIn("synthetic-cp",str(child))

    def test_sanitized_real_child_process_has_only_gateway_credential(self):
        import subprocess
        import sys
        env=services._gateway_child_environment(
            {"TELEGRAM_BOT_TOKEN":"synthetic-telegram-do-not-print"},
            {"PATH":os.environ.get("PATH",""),
             "SYSTEMROOT":os.environ.get("SYSTEMROOT",""),
             "CONTROL_PLANE_API_KEY":"synthetic-cp-do-not-print",
             "HERMES_A4_OBSERVER_AUTHORIZATION":"Bearer synthetic-observer",
             "MCP_EXTRA_HEADERS":"Authorization: synthetic"})
        # The worker prints only booleans, never environment values.
        code=(
            "import json,os;"
            "print(json.dumps({k:k in os.environ for k in "
            "['TELEGRAM_BOT_TOKEN','CONTROL_PLANE_API_KEY',"
            "'HERMES_A4_OBSERVER_AUTHORIZATION','MCP_EXTRA_HEADERS']}))"
        )
        child=subprocess.run([sys.executable,"-c",code],env=env,
                             capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,0,child.stderr)
        flags=__import__("json").loads(child.stdout)
        self.assertEqual(flags,{
            "TELEGRAM_BOT_TOKEN":True,
            "CONTROL_PLANE_API_KEY":False,
            "HERMES_A4_OBSERVER_AUTHORIZATION":False,
            "MCP_EXTRA_HEADERS":False,
        })
        self.assertNotIn("synthetic-",child.stdout+child.stderr)

    def test_gateway_process_environment_denies_missing_telegram(self):
        with self.assertRaisesRegex(services.StartupBlocked,"telegram_bot_token_missing"):
            services._gateway_child_environment({},{"CONTROL_PLANE_API_KEY":"synthetic"})

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

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell required")
    def test_explicit_dev_mode_bypasses_only_a4_gate(self):
        import subprocess
        with TemporaryDirectory() as td:
            state = Path(td) / "Hermes-OneClick"
            state.mkdir()
            settings = state / "settings-a4.json"
            original = '{"schema_version":1,"mode":"read_only"}'
            settings.write_text(original, encoding="utf-8")
            env = os.environ.copy()
            env["LOCALAPPDATA"] = td
            script = Path(__file__).resolve().parents[2] / "quickstart" / "start.ps1"
            cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(script),
                   "-GateCheckOnly"]
            refused = subprocess.run(cmd, env=env, capture_output=True, timeout=20)
            self.assertEqual(refused.returncode, 2)
            self.assertIn(b"A4", refused.stdout)
            allowed = subprocess.run(cmd + ["-DevMode"], env=env,
                                     capture_output=True, timeout=20)
            self.assertEqual(allowed.returncode, 0,
                             allowed.stderr.decode(errors="replace"))
            self.assertIn(b"A4_GATE_OK", allowed.stdout)
            self.assertIn(b"DEV MODE", allowed.stdout)
            self.assertEqual(settings.read_text(encoding="utf-8"), original)
            self.assertEqual(sorted(p.name for p in state.iterdir()),
                             ["settings-a4.json"])

    def test_dev_is_default_during_development_but_strict_is_reversible(self):
        root = Path(__file__).resolve().parents[2]
        dev = (root / "Start-Hermes-DEV.cmd").read_text(encoding="utf-8")
        normal = (root / "Start-Hermes-All.cmd").read_text(encoding="utf-8")
        self.assertIn("-DevMode", dev)
        self.assertIn("-DevMode", normal)
        self.assertIn('if /I "%HERMES_A4_STRICT%"=="1"', normal)
        self.assertIn("quickstart", dev)
        self.assertIn("quickstart", normal)
        for script in (dev, normal):
            self.assertNotIn("setx", script.lower())
            self.assertNotIn("reg add", script.lower())
            self.assertNotIn("icacls", script.lower())
            self.assertNotIn("netsh", script.lower())

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
