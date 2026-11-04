"""Build reviewed, deterministic, secret-free Tunnel ZIPs for public distribution.

No network, no Git push, no browser calls. Run from the repository checkout:
    python scripts/build-distribution.py
"""
from __future__ import annotations
import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
ALLOWED_SUFFIXES = {".py", ".js", ".json", ".html", ".md", ".ps1", ".txt"}
EXCLUDED_SEGMENTS = {
    "bridge-state", ".local-mcp-state", ".local-state", ".agent-team",
    "agent-briefs", "agent-reports", "__pycache__", ".venv", "venv",
    "node_modules", ".git", ".secrets", "local-run-logs", "chrome-profile",
    "browser-user-data", "dist", "temp", "tmp"
}
EXCLUDED_FILENAMES = {
    "config.json", "bridge-config.json", ".env", ".env.local",
    "token", "observer-token", "observer-authorization-header"
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{18,}"),
    re.compile(r"gh(?:p|o|u|s|r)_[A-Za-z0-9]{18,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{18,}"),
)
ZIP_DATE = (2026, 11, 4, 0, 0, 0)
PROJECT = "Tunnel"
REQUIRED = {
    "README.md", "INSTALL_FROM_ARCHIVE.ps1",
    "scripts/install-local.ps1", "scripts/observer_auth.py",
    "scripts/verify-portable-install.py", "scripts/status-stack.py",
    "scripts/chain2-triage.py",
    "chat-launch-smoke-desktop-test/plugin.json",
    "chat-launch-smoke-desktop-test/mcp.json",
    "chat-launch-smoke-desktop-test/server.py",
    "chat-launch-smoke-desktop-test/panel.html",
    "chat-launch-smoke-desktop-test/verify_plugin.py",
    "chat-launch-smoke-desktop-test/web-extension/manifest.json",
    "chat-launch-smoke-desktop-test/web-companion/bridge.py",
    "chat-launch-smoke-desktop-test/web-companion/setup_bridge.py",
    "chat-launch-smoke-desktop-test/web-companion/build_clean_extension.py",
    "chat-launch-smoke-desktop-test/web-companion/chain3_continuous.py",
    "Local-Workspace-Extensions/mcp-host-source/manage.py",
    "Local-Workspace-Extensions/skills/spec-workflow/SKILL.md",
    "docs/ARCHIVE_INSTALL_RU.md",
}

def is_allowed(rel: Path) -> bool:
    parts = rel.parts
    if not parts or any(part in EXCLUDED_SEGMENTS for part in parts):
        return False
    if any(part.startswith(".portable-verification") for part in parts):
        return False
    if rel.name in EXCLUDED_FILENAMES or rel.name.startswith(".env."):
        return False
    if rel.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}:
        return False
    if rel.name == ".gitignore":
        return True
    if rel.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    if parts[0] in ("Local-Workspace-Extensions", "chat-launch-smoke-desktop-test"):
        return True
    if parts[0] == "scripts" and len(parts) == 2:
        return True
    if parts[0] == "docs" and len(parts) == 2:
        return True
    return len(parts) == 1 and rel.name in ("README.md", "INSTALL_FROM_ARCHIVE.ps1")

def load_sources():
    found = {}
    for entry in ROOT.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(ROOT)
        if not is_allowed(rel):
            continue
        if entry.is_symlink() or any(ancestor.is_symlink() for ancestor in entry.parents if ancestor != ROOT):
            raise ValueError("Symlink is not distributable: " + rel.as_posix())
        if not 0 < entry.stat().st_size < 220000:
            raise ValueError("Unexpected source size: " + rel.as_posix())
        data = entry.read_bytes()
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Non-UTF8 source: " + rel.as_posix()) from exc
        if any(pattern.search(decoded) for pattern in SECRET_PATTERNS):
            raise ValueError("Possible secret in source: " + rel.as_posix())
        found[rel.as_posix()] = data
    missing = REQUIRED - found.keys()
    if missing:
        raise ValueError("Incomplete public bundle: " + ", ".join(sorted(missing)))
    if len(found) > 240:
        raise ValueError("Too many source files, review the allowlist")
    return found

def zip_bytes(zipped, selected, prefix, remap=None):
    with zipfile.ZipFile(zipped, mode="w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=False, strict_timestamps=True) as archive:
        for path in sorted(selected):
            mapped = remap(path) if remap is not None else path
            dest = PurePosixPath(prefix) / PurePosixPath(mapped)
            if any(part in ("..", ".", "") for part in dest.parts) or dest.is_absolute():
                raise ValueError("Unsafe ZIP path: " + str(dest))
            zi = zipfile.ZipInfo(dest.as_posix(), date_time=ZIP_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o100644 << 16)
            archive.writestr(zi, selected[path], compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)

def make_packages(files):
    full = files.copy()
    local = {p:content for p,content in files.items()
             if p.startswith("Local-Workspace-Extensions/mcp-host-source/")
             or p in ("Local-Workspace-Extensions/review_merge.py",
                      "scripts/observer_auth.py","scripts/test_observer_auth.py",
                      "README.md","docs/ARCHIVE_INSTALL_RU.md")}
    smoke_names = {
        "server.py","panel.html","plugin.json","mcp.json",
        "verify_plugin.py","launch_one_chat.py",
        "desktop_probe.py","probe_default_browser.py"
    }
    smoke = {p:content for p,content in files.items()
             if p.startswith("chat-launch-smoke-desktop-test/")
             and "/" not in p[len("chat-launch-smoke-desktop-test/"):]
             and p.rsplit("/",1)[-1] in smoke_names}
    smoke["README_SOURCE.md"] = (
        "# New Chat Smoke Test\n\n"
        "These files are the source of a custom MCP App. They do not carry any "
        "existing ChatGPT App ID, authorization or session. On another account, "
        "deploy a new stdio MCP server and configure a new Tunnel custom app; "
        "see the full Tunnel README for steps. Uploading this ZIP to ChatGPT "
        "does not guarantee automatic app registration.\n"
    ).encode()
    web = {p:content for p,content in files.items()
           if p.startswith("chat-launch-smoke-desktop-test/web-companion/")
           or p.startswith("chat-launch-smoke-desktop-test/web-extension/")
           or p in ("README.md","docs/ARCHIVE_INSTALL_RU.md")}
    packages = [
        ("Tunnel-Complete-Windows.zip", full, PROJECT, None),
        ("Local-Workspace-MCP.zip", local, "Local-Workspace-MCP", None),
        ("New-Chat-Smoke-Test.zip", smoke, "New-Chat-Smoke-Test",
         lambda p: p[len("chat-launch-smoke-desktop-test/"):] if p.startswith("chat-launch-smoke-desktop-test/") else p),
        ("Web-Companion-Extension.zip", web, "Web-Companion-Extension",
         lambda p: p[len("chat-launch-smoke-desktop-test/"):] if p.startswith("chat-launch-smoke-desktop-test/") else p)
    ]
    if not DIST.is_dir():
        DIST.mkdir()
    result=[]
    for filename,chosen,prefix,remap in packages:
        output=DIST/filename
        if output.exists() or output.is_symlink():
            raise ValueError("Distribution already exists: "+filename)
        zip_bytes(output,chosen,prefix,remap)
        with zipfile.ZipFile(output,"r") as z:
            all_names=z.namelist()
            if len(all_names)!=len(chosen) or z.testzip() is not None:
                raise ValueError("Corrupted ZIP:"+filename)
            if len(all_names)!=len(set(all_names)):
                raise ValueError("Duplicate archive paths:"+filename)
            for archived in all_names:
                if any(seg in EXCLUDED_SEGMENTS for seg in PurePosixPath(archived).parts):
                    raise ValueError("Runtime state accidentally included:"+archived)
        digest=hashlib.sha256(output.read_bytes()).hexdigest()
        result.append({"file":filename,"sha256":digest,"bytes":output.stat().st_size,
                       "source_files":len(chosen)})
    (DIST/"SHA256SUMS.txt").write_text(
        "".join(item["sha256"]+"  "+item["file"]+"\n" for item in result),encoding="utf-8")
    (DIST/"MANIFEST.json").write_text(json.dumps({
        "schema_version":1,"created_utc":"2026-11-04",
        "description":"Reviewed portable source packages, no credentials or sessions",
        "files":result,
    },indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"total_source_files":len(files),"archives":result,
                      "secret_matches":0},ensure_ascii=True))

def discard_previous_generated_distribution():
    """Only remove a complete previously generated set with valid checksums."""
    manifest=DIST/"MANIFEST.json"
    sumfile=DIST/"SHA256SUMS.txt"
    if not manifest.is_file() or not sumfile.is_file() or manifest.is_symlink():
        raise ValueError("Old distribution missing manifest; do not remove files")
    obj=json.loads(manifest.read_text(encoding="utf-8"))
    records=obj.get("files")
    names={
        "Tunnel-Complete-Windows.zip",
        "Local-Workspace-MCP.zip",
        "New-Chat-Smoke-Test.zip",
        "Web-Companion-Extension.zip"
    }
    if (type(records) is not list or
        {x.get("file") for x in records if type(x) is dict}!=names or len(records)!=4):
        raise ValueError("Unexpected distribution manifest")
    expected=names|{"MANIFEST.json","SHA256SUMS.txt"}
    present={x.name for x in DIST.iterdir()}
    if present!=expected:
        raise ValueError("Unexpected files in dist; refusing rebuild")
    digest_lines=sumfile.read_text(encoding="utf-8")
    for row in records:
        candidate=DIST/row["file"]
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("Old distribution not a regular file")
        digest=hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest!=row["sha256"] or (f"{digest}  {candidate.name}"+chr(10)) not in digest_lines:
            raise ValueError("Old distribution checksum mismatch")
    for row in records:
        (DIST/row["file"]).unlink()
    sumfile.unlink()
    manifest.unlink()


def main():
    if len(sys.argv)>1 and sys.argv[1]=="inventory":
        files=load_sources()
        print(json.dumps({"ok":True,"total_files":len(files),
                          "source_bytes":sum(map(len,files.values())),
                          "examples":list(sorted(files))[:8]}))
    elif len(sys.argv)>1 and sys.argv[1]=="rebuild":
        files=load_sources()
        discard_previous_generated_distribution()
        make_packages(files)
    elif len(sys.argv)==1 or sys.argv[1]=="build":
        make_packages(load_sources())
    else:
        raise ValueError("Unknown action")

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)[:300]}))
        sys.exit(1)
