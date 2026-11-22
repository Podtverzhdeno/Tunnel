"""Executes opt-in live MCP SDK handshake inside the isolated CDP venv."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/".venv-cdp"/"Scripts"/"python.exe"
TEST=ROOT/"planning"/"test_hermes_live_mcp_stdio.py"
if not PY.is_file():
    print(json.dumps({"ok":False,"reason":"cdp_venv_missing"}))
    raise SystemExit(2)
try:
    completed=subprocess.run([str(PY),str(TEST)],cwd=ROOT,
        capture_output=True,text=True,encoding="utf8",errors="replace",timeout=50)
    line=completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "{}"
    try:result=json.loads(line)
    except ValueError:result={"ok":False,"reason":"invalid_sdk_receipt"}
    receipt={"ok":bool(result.get("ok")) and completed.returncode==0,
             "code":completed.returncode,"result":result,
             "used_existing_browser":False,"used_existing_telegram":False}
    print(json.dumps(receipt,ensure_ascii=False))
    raise SystemExit(0 if receipt["ok"] else 1)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok":False,"reason":"sdk_handshake_timeout"}))
    raise SystemExit(2)
