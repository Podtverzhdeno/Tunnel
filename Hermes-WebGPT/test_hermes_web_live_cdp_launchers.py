"""Static and native Windows PowerShell validation of the CDP launchers.

Does NOT execute the launchers: no browser, Relay, login, network or
user state is touched.
"""
from __future__ import annotations
import base64
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
SCRIPTS = ("Start-Hermes-CDP-Browser.ps1", "Start-Hermes-MCP-Web.ps1")


class LauncherTests(unittest.TestCase):
    def test_expected_extension_free_entrypoints(self):
        browser = (ROOT / SCRIPTS[0]).read_text(encoding="utf-8-sig")
        worker = (ROOT / SCRIPTS[1]).read_text(encoding="utf-8-sig")
        self.assertIn("--disable-extensions", browser)
        self.assertIn("remote-debugging-address=127.0.0.1", browser)
        self.assertIn("Start-Process -FilePath $browser", browser)
        self.assertIn("web-chatgpt-high-cdp", worker)
        self.assertIn("-m hermes_cdp_mcp_sidecar", worker)
        self.assertIn("playwright-worker-auth.json", worker)
        self.assertNotIn("Web Companion", worker)
        self.assertNotIn("relay.py --data", worker)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "native Windows PowerShell parser required")
    def test_native_powershell_parser_accepts_both_scripts(self):
        for script in SCRIPTS:
            path = ROOT / script
            source = r"""
$ErrorActionPreference='Stop'
$errorsSeen=$null
$tokens=$null
$tree=[System.Management.Automation.Language.Parser]::ParseFile(
    $env:HERMES_CDP_PARSE_FILE, [ref]$tokens, [ref]$errorsSeen)
if($errorsSeen.Count -ne 0){throw 'launcher_powershell_syntax_invalid'}
Write-Output 'CDP_PS_PARSE_OK'
"""
            import os as _os
            env = _os.environ.copy()
            env["HERMES_CDP_PARSE_FILE"] = str(path)
            encoded = base64.b64encode(source.encode("utf-16le")).decode("ascii")
            run = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", encoded],
                capture_output=True, text=True, encoding="utf8", errors="replace",
                timeout=15, env=env
            )
            self.assertEqual(run.returncode, 0, (script, run.stderr[-500:]))
            self.assertIn("CDP_PS_PARSE_OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
