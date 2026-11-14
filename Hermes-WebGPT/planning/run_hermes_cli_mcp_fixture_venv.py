"""Run isolated actual Hermes CLI->MCP SDK->Playwright end-to-end test."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
py=root/".venv-cdp"/"Scripts"/"python.exe"
script=root/"planning"/"hermes_cli_mcp_cdp_fixture_e2e.py"
if not py.is_file():
    print(json.dumps({"ok":False,"reason":"isolated_playwright_python_missing"}))
    raise SystemExit(2)
try:
    r=subprocess.run([str(py),str(script)],cwd=root,capture_output=True,
                     text=True,encoding="utf8",errors="replace",timeout=145)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok":False,"reason":"isolated_mcp_cli_test_timeout"}))
    raise SystemExit(2)
lines=r.stdout.strip().splitlines()
try: receipt=json.loads(lines[-1]) if lines else {"ok":False}
except ValueError: receipt={"ok":False,"reason":"non_json_cli_receipt"}
print(json.dumps({"returncode":r.returncode,"receipt":receipt,
                  "local_test_only":True},ensure_ascii=False))
raise SystemExit(0 if r.returncode==0 and receipt.get("ok") else 1)
