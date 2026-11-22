"""Actual installed Hermes MCP manager: isolated config and optional SDK via test venv.

Do not touch running ~/.hermes, Gateway, services, production profile or credentials.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
inst=Path(os.environ.get("LOCALAPPDATA",""))/"hermes"/"hermes-agent"
sdk=ROOT/".venv-cdp"/"Lib"/"site-packages"
python_cdp=ROOT/".venv-cdp"/"Scripts"/"python.exe"
if not all(p.exists() for p in (inst/"tools"/"mcp_tool.py",sdk,python_cdp)):
    print(json.dumps({"ok":False,"reason":"Hermes_or_isolated_MCP_SDK_missing"}))
    raise SystemExit(2)
sys.path.insert(0,str(inst))
sys.path.append(str(inst/"venv"/"Lib"/"site-packages"))
sys.path.append(str(sdk))
with tempfile.TemporaryDirectory(prefix="hermes-mcp-registry-fixture-") as directory:
    previous=os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"]=directory
    tools_module=None
    try:
        import tools.mcp_tool as tools_module
        available=getattr(tools_module,"_MCP_AVAILABLE",False)
        if not available:
            print(json.dumps({"ok":False,"reason":"Hermes_did_not_detect_optional_MCP_SDK"}))
            raise SystemExit(2)
        config={
            "enabled": True,
            "command": str(python_cdp),
            "args": [str(ROOT/"hermes_web_cdp_mcp.py")],
            "env": {"HERMES_CDP_FIXTURE_ONLY":"1"},
            "tools": {"include": [
                "hermes_web_transport_status","hermes_web_process_fixture_once",
            ]},
            "timeout": 30,
        }
        names=tools_module.register_mcp_servers({"hermes_web_cdp_fixture": config})
        kinds=[name for name in names if "hermes_web" in name.lower()]
        result={"ok":len(kinds)>=2,"registered_tool_names":kinds,
                "Hermes_native_MCP_manager":True,
                "sdk_detected":available,
                "isolated_HERMES_HOME":True,
                "existing_profile_modified":False,
                "browser_extension_required":False,
                "no_real_chatgpt":True}
        print(json.dumps(result,ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"ok":False,"reason":"isolated_Hermes_MCP_registry_failed",
                          "error_type":type(error).__name__}))
        raise SystemExit(1)
    finally:
        if tools_module is not None:
            try:tools_module.shutdown_mcp_servers()
            except Exception:pass
            # The native manager keeps a shared stderr file handle open even
            # after shutdown; close it before removing this temporary profile.
            handle=getattr(tools_module, "_mcp_stderr_log_fh", None)
            if handle is not None and handle not in (sys.stderr,sys.stdout):
                try:handle.close()
                except OSError:pass
        if previous is None:os.environ.pop("HERMES_HOME",None)
        else:os.environ["HERMES_HOME"]=previous
