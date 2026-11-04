"""Offline release smoke-test for a clean clone. Never contacts ChatGPT or starts agents."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent.parent
SECRET_PATTERNS=(
    re.compile(r"sk-[A-Za-z0-9_-]{18,}"),
    re.compile(r"gh(?:p|o|s|u|r)_[A-Za-z0-9]{18,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{18,}"),
)
DENY_PARTS={".agent-team","bridge-state","agent-briefs","agent-reports",
            ".local-mcp-state","__pycache__",".env",".secrets"}
EXT_FILES={"background.js","background_content.js","content.js","core.js",
           "manifest.json","popup.html","popup.js"}

def check(cond,msg):
    if not cond: raise AssertionError(msg)

def call(args,cwd,timeout=40):
    result=subprocess.run([sys.executable,*args],cwd=cwd,stdin=subprocess.DEVNULL,
                          text=True,capture_output=True,encoding="utf-8",
                          errors="replace",timeout=timeout,env={**os.environ,
                          "PYTHONDONTWRITEBYTECODE":"1"})
    if result.returncode:
        raise AssertionError(
            f"{Path(args[0]).name} returned {result.returncode}: "
            f"{(result.stderr or result.stdout)[-600:]}")
    return result.stdout

def check_public_source():
    needed=[
        "README.md", ".gitignore","scripts/install-local.ps1",
        "scripts/observer_auth.py","scripts/test_observer_auth.py",
        "scripts/status-stack.py","scripts/test_status_stack.py",
        "Local-Workspace-Extensions/mcp-host-source/manage.py",
        "Local-Workspace-Extensions/mcp-host-source/workspace_mcp/tools.py",
        "Local-Workspace-Extensions/skills/spec-workflow/SKILL.md",
        "chat-launch-smoke-desktop-test/server.py",
        "chat-launch-smoke-desktop-test/plugin.json",
        "chat-launch-smoke-desktop-test/mcp.json",
        "chat-launch-smoke-desktop-test/web-companion/setup_bridge.py",
        "chat-launch-smoke-desktop-test/web-companion/build_clean_extension.py",
        "chat-launch-smoke-desktop-test/web-companion/chain3_continuous.py",
        "chat-launch-smoke-desktop-test/web-extension/manifest.json"
    ]
    for rel in needed:
        check((ROOT/rel).is_file(), "Missing expected source: "+rel)
    gitignore=(ROOT/".gitignore").read_text(encoding="utf-8")
    for name in ("bridge-config.json","bridge-state","config.json",
                 ".agent-team",".env"):
        check(name in gitignore,"Missing ignore guard: "+name)
    extension=ROOT/"chat-launch-smoke-desktop-test"/"web-extension"
    allowed={p.name for p in extension.iterdir() if p.is_file()}
    check(allowed==EXT_FILES,"Source extension contains private/extra files")
    manifest=json.loads((extension/"manifest.json").read_text(encoding="utf-8"))
    check(manifest.get("manifest_version")==3,"Browser manifest invalid")
    check(manifest.get("host_permissions")==[
        "http://127.0.0.1:18763/*","https://chatgpt.com/*"],
        "Browser origin allowlist unexpected")
    examined=0
    for file in ROOT.rglob("*"):
        if not file.is_file():continue
        rel=file.relative_to(ROOT)
        if any(p in DENY_PARTS for p in rel.parts):continue
        if file.suffix not in (".py",".ps1",".md",".json",".js",".html")             and file.name!=".gitignore":
            continue
        if file.is_symlink():raise AssertionError("Linked source: "+str(rel))
        raw=file.read_text(encoding="utf-8")
        if any(p.search(raw) for p in SECRET_PATTERNS):
            raise AssertionError("Potential credential in: "+str(rel))
        examined+=1
    return examined

def clean_install(tmp):
    source=ROOT/"Local-Workspace-Extensions"/"mcp-host-source"
    host=tmp/"local-workspace"
    shutil.copytree(source,host,ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "config.json",".local-mcp-state"))
    projects=tmp/"empty-projects"
    projects.mkdir()
    check(not (host/"config.json").exists(),"Unexpected preinstalled host config")
    call(["manage.py","init"],host)
    check((host/"config.json").exists(),"MCP config not created")
    call(["manage.py","add-root","--name","projects","--path",str(projects)],host)
    doc=json.loads(call(["manage.py","doctor"],host))
    check(doc.get("roots") and doc.get("mcp_initialize"),
          "MCP doctor incomplete")
    roots=json.loads(call(["manage.py","list-roots"],host))
    check(len(roots)==1 and roots[0]["name"]=="projects","MCP alias mismatch")
    check(roots[0]["write"] is False and roots[0]["execute"] is False,
          "Unsafe default MCP permissions")
    return len(doc.get("tools",[])) if isinstance(doc.get("tools"),list)         else doc.get("tools")

def check_observer_transport(host):
    """Validate actual observer HTTP auth, not merely manage.py doctor."""
    import socket
    import time
    from urllib import error, request

    auth_script=ROOT/"scripts"/"observer_auth.py"
    check(auth_script.is_file(),"Missing observer launcher")
    created=json.loads(call([str(auth_script),"init","--host-root",str(host)],ROOT))
    check(created.get("state")=="created","Observer init did not create new secret")
    repeated=json.loads(call([str(auth_script),"init","--host-root",str(host)],ROOT))
    check(repeated.get("state")=="already_initialized","Observer setup not idempotent")
    header=(host/".local-mcp-state"/"observer-authorization-header").read_text("ascii")
    check(header.startswith("Bearer ") and len(header)>60,
          "Observer bearer not generated")
    check(header not in json.dumps(created)+json.dumps(repeated),
          "Observer bearer was printed")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    proc=subprocess.Popen(
        [sys.executable,str(auth_script),"serve","--host-root",str(host),
         "--port",str(port)],cwd=ROOT,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f"http://127.0.0.1:{port}"
    try:
        ok=False
        for _ in range(45):
            if proc.poll() is not None:
                raise AssertionError("Observer server stopped before health check")
            try:
                with request.urlopen(url+"/healthz",timeout=.4) as response:
                    status=json.load(response)
                if status.get("status")=="ok":
                    ok=True
                    break
            except (OSError,ValueError):
                time.sleep(.1)
        check(ok,"Observer MCP server did not become healthy")
        payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                             "params":{"protocolVersion":"2025-11-25"}}).encode()
        def rpc(auth=None):
            headers={"Content-Type":"application/json"}
            if auth is not None:
                headers["Authorization"]=auth
            req=request.Request(url+"/mcp",data=payload,headers=headers)
            with request.urlopen(req,timeout=3) as response:
                return json.load(response)
        for bad in (None,"Bearer wrong"):
            try:
                rpc(bad)
            except error.HTTPError as exc:
                check(exc.code==401,"Unauthorized request not rejected")
            else:
                raise AssertionError("Unauthorized MCP call succeeded")
        valid=rpc(header)
        check(valid.get("result",{}).get("protocolVersion")=="2025-11-25",
              "Authenticated observer initialize failed")
        return "authorized_200_unauthorized_401"
    finally:
        proc.terminate()
        try:proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)


def clean_companion(tmp):
    source=ROOT/"chat-launch-smoke-desktop-test"
    dest=tmp/"chat-launch-smoke-desktop-test"
    dest.mkdir()
    for name in ("server.py","panel.html","plugin.json","mcp.json",
                 "verify_plugin.py"):
        shutil.copy2(source/name,dest/name)
    # Use only distributed files; never copy user's bridge key/config.
    shutil.copytree(source/"web-companion",dest/"web-companion",
                    ignore=shutil.ignore_patterns("__pycache__","bridge-state",
                      "bridge-config.json","*.pyc"))
    shutil.copytree(source/"web-extension",dest/"web-extension",
                    ignore=shutil.ignore_patterns("__pycache__","bridge-config.json"))
    check(not (dest/"web-companion"/"bridge-state").exists(),
          "Companion contains shared state")
    call(["verify_plugin.py"],dest)
    c=dest/"web-companion"
    setup=call(["setup_bridge.py"],c)
    check("key" not in setup.lower(),"Bridge setup leaked key label")
    call(["build_clean_extension.py"],c)
    token=(c/"bridge-state"/"token").read_text(encoding="utf-8")
    cfg=json.loads((c/"bridge-config.json").read_text(encoding="utf-8"))
    copy=json.loads((dest/"web-extension"/"bridge-config.json").read_text(encoding="utf-8"))
    check(len(token)>=32 and token==cfg["key"]==copy["key"],
          "Independent local bridge key mismatch")
    check(cfg["endpoint"]=="http://127.0.0.1:18763","Bad private bridge URL")
    check(token not in setup,"Secret appeared in CLI output")
    # Clean clone is unarmed; do not create new conversations.
    chain=json.loads(call(["chain3_continuous.py","preview"],c))
    check(chain.get("owner_armed") is False,"Unexpected Chain3 arm in clean clone")
    check(chain.get("no_jobs_created") is True,
          "Preview must be read-only")
    return {"browser_extension_version":
        json.loads((dest/"web-extension"/"manifest.json").read_text("utf-8"))["version"],
        "chain3_selected_mode":chain["selected_mode"],
        "owner_armed":chain["owner_armed"]}

def check_powershell_install(tmp):
    """Run actual PowerShell quickstart twice in a disposable Windows fixture."""
    if os.name!="nt":return "skipped_non_windows"
    installer=ROOT/"scripts"/"install-local.ps1"
    check(installer.is_file(),"Installer missing")
    fixture=tmp/"powershell-fixture"
    shutil.copytree(ROOT/"Local-Workspace-Extensions"/"mcp-host-source",
                    fixture/"Local-Workspace-Extensions"/"mcp-host-source",
                    ignore=shutil.ignore_patterns("__pycache__","*.pyc","config.json",
                                                  ".local-mcp-state"))
    dest=fixture/"chat-launch-smoke-desktop-test"
    dest.mkdir()
    shutil.copytree(ROOT/"chat-launch-smoke-desktop-test"/"web-companion",
                    dest/"web-companion",
                    ignore=shutil.ignore_patterns("__pycache__","bridge-state",
                                                  "bridge-config.json","*.pyc"))
    shutil.copytree(ROOT/"chat-launch-smoke-desktop-test"/"web-extension",
                    dest/"web-extension",
                    ignore=shutil.ignore_patterns("bridge-config.json"))
    scripts=fixture/"scripts"
    scripts.mkdir()
    shutil.copy2(installer,scripts/"install-local.ps1")
    shutil.copy2(ROOT/"scripts"/"observer_auth.py",scripts/"observer_auth.py")
    workspace=fixture/"projects"
    workspace.mkdir()
    cmd=["powershell.exe","-NoProfile","-NonInteractive",
         "-ExecutionPolicy","Bypass","-File",
         str(scripts/"install-local.ps1"),"-ProjectsPath",str(workspace)]
    observer_path=(fixture/"Local-Workspace-Extensions"/"mcp-host-source"/
                   ".local-mcp-state"/"observer-authorization-header")
    first_header=None
    for _ in range(2):
        completed=subprocess.run(cmd,cwd=fixture,stdin=subprocess.DEVNULL,
                                 text=True,capture_output=True,timeout=40,
                                 encoding="utf-8",errors="replace")
        if completed.returncode:
            raise AssertionError("PowerShell offline quickstart failed: "+
                                 (completed.stderr or completed.stdout)[-500:])
        check(observer_path.is_file(),"PowerShell installer omitted MCP bearer")
        current_header=observer_path.read_text(encoding="ascii")
        check(current_header.startswith("Bearer "),"Invalid installed MCP bearer")
        check(current_header not in completed.stdout,"Installer printed MCP bearer")
        if first_header is not None:
            check(first_header==current_header,"Installer rotated MCP bearer unexpectedly")
        first_header=current_header
    host=json.loads((fixture/"Local-Workspace-Extensions"/"mcp-host-source"/
                     "config.json").read_text(encoding="utf-8"))
    check(len(host["roots"])==1,"Repeated installer duplicated MCP alias")
    key=(dest/"web-companion"/"bridge-state"/"token").read_text("utf-8")
    extension=json.loads((dest/"web-extension"/"bridge-config.json").read_text("utf-8"))
    check(len(key)>=32 and extension["key"]==key,
          "PowerShell installer bridge key mismatch")
    return "passed_twice_idempotent"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-secret-scan",action="store_true")
    parser.add_argument("--test-powershell",action="store_true",
                        help="Also invoke the Windows setup script twice in an isolated copy")
    args=parser.parse_args()
    if sys.version_info<(3,10):raise RuntimeError("Python >=3.10 is required")
    count=0 if args.skip_secret_scan else check_public_source()
    # Disposable fixture is inside the clone; no OS service/network calls.
    with tempfile.TemporaryDirectory(prefix=".portable-verification-",dir=ROOT) as directory:
        root=Path(directory)
        tools=clean_install(root)
        mcp_http_auth=check_observer_transport(root/"local-workspace")
        smoke=clean_companion(root)
        powershell=check_powershell_install(root) if args.test_powershell else "not_requested"
    print(json.dumps({"ok":True,"source_files_scanned":count,
                      "mcp_http_auth":mcp_http_auth,
                      "powershell_installer":powershell,
                      "local_mcp_tools":tools,
                      "smoke_app_tests":"passed",
                      "browser_bridge_setup":"passed",
                      "no_live_browser_calls":True,
                      **smoke},ensure_ascii=True))
if __name__=="__main__":
    try:main()
    except (OSError,AssertionError,ValueError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok":False,"error":str(exc)[:350]},ensure_ascii=True))
        raise SystemExit(1)
