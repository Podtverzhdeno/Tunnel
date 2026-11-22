"""Launch the isolated MCP CDP fixture test in existing project-local Playwright venv.

No installation, gateway restart, browser profile reuse or network beyond local fixture.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/".venv-cdp"/"Scripts"/"python.exe"
if not PY.is_file():
    print(json.dumps({"ok":False,"reason":"project_local_playwright_venv_missing"}))
    raise SystemExit(2)
script=ROOT/"planning"/"cdp_mcp_fixture_e2e.py"
try:
    result=subprocess.run([str(PY),str(script)],cwd=ROOT,stdin=subprocess.DEVNULL,
                          text=True,capture_output=True,timeout=150,
                          encoding="utf8",errors="replace")
    lines=result.stdout.splitlines()
    last=lines[-1] if lines else "{}"
    try: value=json.loads(last)
    except ValueError: value={"ok":False,"reason":"fixture_receipt_not_json"}
    print(json.dumps({"local_venv":True,"child_returncode":result.returncode,
                      "result":value},ensure_ascii=False))
    raise SystemExit(0 if result.returncode==0 and value.get("ok") else 1)
except subprocess.TimeoutExpired:
    print(json.dumps({"ok":False,"reason":"fixture_e2e_timeout","local_venv":True}))
    raise SystemExit(2)
