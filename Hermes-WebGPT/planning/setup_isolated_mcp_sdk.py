"""Install the Hermes-declared optional MCP client SDK in test-only .venv-cdp.
Does not touch the live Hermes venv, profile or Gateway.
"""
from __future__ import annotations
import importlib.metadata as md
import json
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
py=root/".venv-cdp"/"Scripts"/"python.exe"
if not py.is_file():
    print(json.dumps({"ok":False,"reason":"isolated_venv_missing"}))
    raise SystemExit(2)
result=subprocess.run(
    [str(py),"-m","pip","install","--disable-pip-version-check","--no-input",
     "mcp>=1.2.0,<2"],
    cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
    errors="replace",timeout=150)
if result.returncode:
    print(json.dumps({"ok":False,"reason":"isolated_mcp_sdk_install_failed",
                      "code":result.returncode,"error_type":"pip_install_failed"}))
    raise SystemExit(2)
v=subprocess.run([str(py),"-c","import importlib.metadata; print(importlib.metadata.version('mcp'))"],
                 cwd=root,capture_output=True,text=True,timeout=20)
print(json.dumps({"ok":v.returncode==0,"sdk_version":v.stdout.strip(),
                  "scope":"project_local_cdp_venv","live_hermes_modified":False}))
raise SystemExit(v.returncode)
