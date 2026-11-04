"""Build a non-secret, content-addressed P085 broker release candidate.

This is NOT an installer, service, authorization, signed owner approval or
protected Windows directory. It collects only reviewed-design *candidate*
Python modules from the existing broker kernel, with no keys/config/state.
Runs inside the currently allowed project workspace, never touches host/tunnel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/"experiments"/"foundation_kernel"
DEFAULT_DEST=ROOT/".agent-team"/"p085-release-candidates"
SCHEMA="p085-keyless-source-candidate-v1"
MAX_FILES=60
MAX_CANDIDATES=40
MAX_BYTES=4*1024*1024
MAX_FILE_BYTES=256*1024
REQUIRED=frozenset({
    "broker_named_pipe.py","broker_supervisor.py","windows_isolation.py",
    "win_pipe_ipc.py","win_pipe_overlapped.py","broker_stdio.py",
    "signed_policy.py","host_auth.py","journal.py",
})

class BundleRejected(ValueError):
    pass

def _safe_regular(file:Path)->bool:
    return (file.is_file() and not file.is_symlink()
            and not getattr(file,"is_junction",lambda:False)())

def _safe_directory(folder:Path)->bool:
    return (folder.is_dir() and not folder.is_symlink()
            and not getattr(folder,"is_junction",lambda:False)())

def inventory(source:Path=SOURCE)->dict[str,bytes]:
    """Read a shallow allowlisted source tree; no symlink traversal or secrets."""
    if not _safe_directory(source):
        raise BundleRejected("SOURCE_NOT_REGULAR_DIRECTORY")
    files={}
    total=0
    for file in sorted(source.iterdir(),key=lambda x:x.name):
        if file.is_symlink() or getattr(file,"is_junction",lambda:False)():
            raise BundleRejected("SOURCE_LINK_DENIED")
        if not file.name.endswith(".py") or file.name.startswith("test_"):
            continue
        if file.name=="__init__.py" or re.fullmatch(r"[a-z][a-z0-9_]*\.py",file.name):
            if not _safe_regular(file):
                raise BundleRejected("SOURCE_FILE_INVALID")
            size=file.stat().st_size
            if size>MAX_FILE_BYTES:
                raise BundleRejected("SOURCE_FILE_OVERSIZED")
            raw=file.read_bytes()
            if file.stat().st_size!=len(raw) or not _safe_regular(file):
                raise BundleRejected("SOURCE_CHANGED_WHILE_READING")
            if b"\0" in raw:
                raise BundleRejected("SOURCE_NOT_TEXT")
            files[file.name]=raw
            total+=len(raw)
        if len(files)>MAX_FILES or total>MAX_BYTES:
            raise BundleRejected("SOURCE_BUDGET_EXCEEDED")
    if not REQUIRED.issubset(files):
        raise BundleRejected("REQUIRED_BROKER_MODULES_MISSING")
    return files

def manifest_for(files:dict[str,bytes])->dict:
    digests={key:hashlib.sha256(value).hexdigest()
             for key,value in sorted(files.items())}
    canonical=json.dumps(digests,sort_keys=True,separators=(",",":")).encode("ascii")
    identity=hashlib.sha256(canonical).hexdigest()
    return {"schema":SCHEMA,"candidate_id":identity,
            "files_sha256":digests,"file_count":len(files),
            "release_approved":False,"os_acl_verified":False,
            "trusted_issuer_approved":False,"live_host_modified":False}

def verify_candidate(path:Path)->dict:
    if not _safe_directory(path):
        raise BundleRejected("CANDIDATE_PATH_INVALID")
    manifest_file=path/"MANIFEST.json"
    if not _safe_regular(manifest_file) or manifest_file.stat().st_size>16000:
        raise BundleRejected("CANDIDATE_MANIFEST_INVALID")
    try:
        saved=json.loads(manifest_file.read_text(encoding="ascii"))
    except (OSError,UnicodeError,ValueError):
        raise BundleRejected("CANDIDATE_MANIFEST_INVALID") from None
    # The manifest must bind the *entire* candidate tree, not just the
    # Python files inventory() selects. Hidden policy files, bytecode,
    # symlinked DLLs or test_*.py imports must not sneak into deployment.
    claimed=saved.get("files_sha256") if type(saved) is dict else None
    if type(claimed) is not dict:
        raise BundleRejected("CANDIDATE_MANIFEST_INVALID")
    entries=list(path.iterdir())
    if any(entry.is_symlink() or getattr(entry,"is_junction",lambda:False)() or
           not entry.is_file() for entry in entries):
        raise BundleRejected("CANDIDATE_UNSAFE_EXTRA_ENTRY")
    if {entry.name for entry in entries}!=set(claimed)|{"MANIFEST.json"}:
        raise BundleRejected("CANDIDATE_UNEXPECTED_EXTRA_FILE")
    expected={key:hashlib.sha256(data).hexdigest()
              for key,data in inventory(path).items()}
    original=manifest_for({key:(path/key).read_bytes() for key in expected})
    if saved!=original or saved.get("files_sha256")!=expected:
        raise BundleRejected("CANDIDATE_DIGEST_DRIFT")
    return saved

def build_candidate(source:Path=SOURCE,dest_root:Path=DEFAULT_DEST)->dict:
    files=inventory(source)
    manifest=manifest_for(files)
    if not _safe_directory(dest_root.parent):
        raise BundleRejected("DEST_PARENT_INVALID")
    if dest_root.exists() and not _safe_directory(dest_root):
        raise BundleRejected("DEST_ROOT_UNSAFE")
    dest_root.mkdir(exist_ok=True)
    target=dest_root/manifest["candidate_id"]
    if target.is_symlink() or getattr(target,"is_junction",lambda:False)():
        raise BundleRejected("CANDIDATE_LINK_DENIED")
    if target.exists():
        if verify_candidate(target)!=manifest:
            raise BundleRejected("EXISTING_CANDIDATE_CHANGED")
        return {"created":False,"candidate_id":manifest["candidate_id"],
                "file_count":len(files),"release_approved":False,
                "live_host_modified":False}
    existing=[
        entry for entry in dest_root.iterdir()
        if re.fullmatch(r"[a-f0-9]{64}",entry.name)
    ]
    if len(existing)>=MAX_CANDIDATES:
        raise BundleRejected("CANDIDATE_HISTORY_CAPACITY_REACHED")
    with tempfile.TemporaryDirectory(dir=dest_root,prefix=".p085-staging-") as temp:
        stage=Path(temp)/"bundle"
        stage.mkdir()
        for name,data in files.items():
            (stage/name).write_bytes(data)
        (stage/"MANIFEST.json").write_text(
            json.dumps(manifest,sort_keys=True,indent=2)+"\n",encoding="ascii")
        if verify_candidate(stage)!=manifest:
            raise BundleRejected("CANDIDATE_VERIFY_FAILED")
        if target.exists():
            raise BundleRejected("CANDIDATE_RACE_COLLISION")
        stage.rename(target)
    # Post-copy verification only; cannot establish security of a
    # user-writable staging directory or that a privileged installer trusts it.
    verify_candidate(target)
    return {"created":True,"candidate_id":manifest["candidate_id"],
            "file_count":len(files),"release_approved":False,
            "live_host_modified":False}

def main()->int:
    try:
        print(json.dumps({"ok":True,**build_candidate()},
                         ensure_ascii=True,sort_keys=True))
        return 0
    except (BundleRejected,OSError) as exc:
        code=str(exc) if type(exc) is BundleRejected else "CANDIDATE_IO_ERROR"
        print(json.dumps({"ok":False,"error":code},sort_keys=True))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
