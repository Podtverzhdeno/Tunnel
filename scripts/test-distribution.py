"""Offline validation of the published ZIP payloads, including a clean Windows import."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent
DIST=ROOT/"dist"
EXPECTED={
    "Tunnel-Complete-Windows.zip": "Tunnel",
    "Local-Workspace-MCP.zip": "Local-Workspace-MCP",
    "New-Chat-Smoke-Test.zip": "New-Chat-Smoke-Test",
    "Web-Companion-Extension.zip": "Web-Companion-Extension"
}
NO_STATE={"bridge-state", ".local-mcp-state", ".agent-team",
          "agent-briefs","agent-reports","__pycache__",".git",".env"}
REQUIRED_FULL=[
    "Tunnel/README.md",
    "Tunnel/INSTALL_FROM_ARCHIVE.ps1",
    "Tunnel/scripts/install-local.ps1",
    "Tunnel/scripts/observer_auth.py",
    "Tunnel/scripts/verify-portable-install.py",
    "Tunnel/chat-launch-smoke-desktop-test/plugin.json",
    "Tunnel/chat-launch-smoke-desktop-test/mcp.json",
    "Tunnel/chat-launch-smoke-desktop-test/server.py",
    "Tunnel/chat-launch-smoke-desktop-test/web-extension/manifest.json",
    "Tunnel/Local-Workspace-Extensions/mcp-host-source/manage.py",
]


def run(args,cwd,timeout=120):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,
                     stdin=subprocess.DEVNULL,encoding="utf-8",
                     errors="replace",timeout=timeout)
    if p.returncode:
        raise AssertionError("command failed ("+str(p.returncode)+"): "+
                             " ".join(map(str,args[:3]))+" "+(p.stderr or p.stdout)[-750:])
    return p.stdout

def archive_audit():
    manifest=json.loads((DIST/"MANIFEST.json").read_text("utf-8"))
    assert manifest.get("schema_version")==1
    rows=manifest["files"]
    assert len(rows)==4 and set(x["file"] for x in rows)==set(EXPECTED)
    hashes=(DIST/"SHA256SUMS.txt").read_text("utf-8")
    info={}
    for row in rows:
        filename=row["file"]
        path=DIST/filename
        assert path.is_file() and not path.is_symlink()
        data=path.read_bytes()
        sha=hashlib.sha256(data).hexdigest()
        assert row["sha256"]==sha and row["bytes"]==len(data),filename
        assert f"{sha}  {filename}\n" in hashes,filename
        with zipfile.ZipFile(path,"r") as z:
            names=z.namelist()
            assert z.testzip() is None and len(names)==len(set(names))
            assert len(names)==row["source_files"]
            assert all(x.startswith(EXPECTED[filename]+"/") for x in names)
            for zi in z.infolist():
                name=PurePosixPath(zi.filename)
                assert not name.is_absolute()
                assert not any(seg in ("..",".") or seg in NO_STATE
                               for seg in name.parts),zi.filename
                assert not (zi.external_attr >> 16) & 0o170000 == 0o120000
        info[filename]={"files":len(names),"bytes":len(data)}
        if filename=="Tunnel-Complete-Windows.zip":
            for needed in REQUIRED_FULL:
                assert needed in names,needed
        if filename=="New-Chat-Smoke-Test.zip":
            for needed in ("plugin.json","mcp.json","server.py","panel.html"):
                assert EXPECTED[filename]+"/"+needed in names
        if filename=="Web-Companion-Extension.zip":
            assert "Web-Companion-Extension/web-extension/manifest.json" in names
    return info


def clean_full_install():
    with tempfile.TemporaryDirectory(prefix="tunnel-zip-smoke-") as temp:
        folder=Path(temp)
        with zipfile.ZipFile(DIST/"Tunnel-Complete-Windows.zip","r") as z:
            z.extractall(folder)
        root=folder/"Tunnel"
        check=run([sys.executable,str(root/"scripts"/"verify-portable-install.py"),
                   "--test-powershell"],root)
        parsed=json.loads(check)
        assert parsed["ok"] and parsed["mcp_http_auth"]=="authorized_200_unauthorized_401"
        assert parsed["powershell_installer"]=="passed_twice_idempotent"
        assert parsed["no_live_browser_calls"] is True
        tests={}
        for name in ("test_chain2_triage.py","test_status_stack.py","test_observer_auth.py"):
            x=run([sys.executable,str(root/"scripts"/name)],root)
            tests[name]="PASS"
        run([sys.executable,str(root/"chat-launch-smoke-desktop-test"/"verify_plugin.py")],root)
        projects=folder/"projects"
        projects.mkdir()
        ps=root/"INSTALL_FROM_ARCHIVE.ps1"
        if os.name=="nt":
            cmd=["powershell.exe","-NoProfile","-NonInteractive",
                 "-ExecutionPolicy","Bypass","-File",str(ps),
                 "-ProjectsPath",str(projects),"-SkipPreflight"]
            a=run(cmd,root)
            private=root/"Local-Workspace-Extensions"/"mcp-host-source"/".local-mcp-state"/"observer-token"
            bridge=root/"chat-launch-smoke-desktop-test"/"web-companion"/"bridge-state"/"token"
            extension=root/"chat-launch-smoke-desktop-test"/"web-extension"/"bridge-config.json"
            assert private.is_file() and bridge.is_file() and extension.is_file()
            first=(private.read_bytes(),bridge.read_bytes())
            assert first[0] not in a.encode() and first[1] not in a.encode()
            run(cmd,root)
            assert first==(private.read_bytes(),bridge.read_bytes())
            tests["powershell_archive_installer"]="PASS_TWICE"
        else:
            tests["powershell_archive_installer"]="SKIPPED_NON_WINDOWS"
        return {"clean_install":parsed,"tests":tests}


def test_components():
    with tempfile.TemporaryDirectory(prefix="tunnel-components-") as temp:
        tmp=Path(temp)
        with zipfile.ZipFile(DIST/"New-Chat-Smoke-Test.zip","r") as z:
            z.extractall(tmp)
        smoke=tmp/"New-Chat-Smoke-Test"
        run([sys.executable,str(smoke/"verify_plugin.py")],smoke)
        with zipfile.ZipFile(DIST/"Web-Companion-Extension.zip","r") as z:
            z.extractall(tmp)
        extension=tmp/"Web-Companion-Extension"/"web-extension"
        manifest=json.loads((extension/"manifest.json").read_text("utf-8"))
        assert manifest["manifest_version"]==3
        assert not (extension/"bridge-config.json").exists()
        with zipfile.ZipFile(DIST/"Local-Workspace-MCP.zip","r") as z:
            z.extractall(tmp)
        local=tmp/"Local-Workspace-MCP"
        assert (local/"Local-Workspace-Extensions"/"mcp-host-source"/"manage.py").is_file()
        assert (local/"scripts"/"observer_auth.py").is_file()
        return {"new_chat_smoke":"PASS","browser_manifest":"PASS",
                "mcp_source":"PASS"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-extract",action="store_true")
    args=parser.parse_args()
    archives=archive_audit()
    smoke=None if args.skip_extract else clean_full_install()
    components=None if args.skip_extract else test_components()
    print(json.dumps({"ok":True,"packages":archives,"full":smoke,
                      "components":components,"no_live_chat_actions":True},
                     ensure_ascii=True))


if __name__=="__main__":
    try: main()
    except Exception as e:
        print(json.dumps({"ok":False,"error":str(e)[:500]}))
        raise SystemExit(1)
