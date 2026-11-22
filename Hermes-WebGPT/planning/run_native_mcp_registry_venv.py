"""Use project test-only MCP SDK interpreter for native Hermes registration."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
py=root/".venv-cdp"/"Scripts"/"python.exe"
script=root/"planning"/"test_hermes_native_mcp_registry_isolated.py"
if not py.is_file():
    print(json.dumps({"ok":False,"reason":"project_sdk_venv_missing"}))
    raise SystemExit(2)
try:
    result=subprocess.run([str(py),str(script)],cwd=root,capture_output=True,
                          text=True,encoding="utf8",errors="replace",timeout=90)
    lines=result.stdout.strip().splitlines()
    try:receipt=json.loads(lines[-1]) if lines else {}
    except ValueError:receipt={"ok":False,"reason":"non_json_native_result"}
    detail = result.stderr.strip().splitlines()
    tail = detail[-1][-180:] if detail else ""
    print(json.dumps({"returncode":result.returncode,"result":receipt,
                      "stderr_last_line":tail,
                      "isolated":True,"live_Hermes_unchanged":True},
                     ensure_ascii=False))
    raise SystemExit(0 if result.returncode==0 and receipt.get("ok") else 1)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok":False,"reason":"native_mcp_registry_timeout"}))
    raise SystemExit(2)
