"""Test the real installed MCP ClientSession in project-local .venv-cdp."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
py=root/".venv-cdp"/"Scripts"/"python.exe"
test=root/"planning"/"test_hermes_mcp_client_stdio.py"
if not py.is_file():
    print(json.dumps({"ok":False,"reason":"cdp_test_venv_missing"}))
    raise SystemExit(2)
try:
    result=subprocess.run([str(py),str(test)],cwd=root,text=True,capture_output=True,
                          encoding="utf8",errors="replace",timeout=50)
    last=result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "{}"
    try: receipt=json.loads(last)
    except ValueError: receipt={"ok":False,"reason":"non_json_sdk_result"}
    print(json.dumps({"ok":bool(receipt.get("ok")) and result.returncode==0,
                      "returncode":result.returncode,"receipt":receipt,
                      "isolated_environment":True,"live_gateway_modified":False},
                     ensure_ascii=False))
    raise SystemExit(0 if receipt.get("ok") and result.returncode==0 else 1)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok":False,"reason":"sdk_handshake_timeout"}))
    raise SystemExit(2)
