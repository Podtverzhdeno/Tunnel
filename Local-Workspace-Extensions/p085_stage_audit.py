"""Read-only structural/ACL healthcheck for a *staged* P085 reviewer package.

No privilege escalation, pipe access, service registration, key reads, or
approval. The stage receipt is written by an agent-editable installer, so it is
NOT trusted owner attestation even after every check passes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from experiments.foundation_kernel.windows_isolation import (
    inspect_acl, SYSTEM_SID, WindowsIsolationError,
)

SCHEMA="p085-protected-code-receipt-v1"
EXPECTED_FILES=frozenset({
    "code/workspace_mcp/__init__.py",
    "code/workspace_mcp/host_pipe_peer.py",
    "code/workspace_mcp/review_grants.py",
    "code/workspace_mcp/review_qa_attestation.py",
    "code/workspace_mcp/review_target_snapshot.py",
    "code/workspace_mcp/review_grant_broker.py",
    "code/workspace_mcp/review_finalizer.py",
    "code/workspace_mcp/trusted_journal_loader.py",
    "code/workspace_mcp/owner_cutover_gate.py",
    "code/workspace_mcp/review_pipe_server.py",
    "code/workspace_mcp/review_pipe_client.py",
    "code/p085_reviewer_service.py",
    "code/p085_trusted/review_merge.py",
})
SAFE_SHA=re.compile(r"[a-f0-9]{64}\Z")
SAFE_SID=re.compile(r"S-1-(?:[0-9]+-){1,16}[0-9]+\Z")
ROOT_ITEMS=frozenset({"code","private_state","STAGE_RECEIPT.json"})
CODE_DIRS=frozenset({"code","code/workspace_mcp","code/p085_trusted"})
MAX_RECEIPT_BYTES=16000
MAX_PY_BYTES=1024*1024

class StageAuditError(ValueError):
    pass

def _regular(file:Path)->bool:
    return file.is_file() and not file.is_symlink() and not getattr(
        file,"is_junction",lambda:False)()

def _directory(path:Path)->bool:
    return path.is_dir() and not path.is_symlink() and not getattr(
        path,"is_junction",lambda:False)()

def _receipt(raw:bytes)->dict:
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise StageAuditError("STAGE_RECEIPT_DUPLICATE_KEY")
            result[key]=value
        return result
    try:
        obj=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
    except StageAuditError:
        raise
    except (UnicodeError,ValueError,RecursionError):
        raise StageAuditError("STAGE_RECEIPT_INVALID") from None
    exact={"schema","plan_sha256","service_sid","reviewer_sid","worker_sid",
           "files","release_approved","live_mcp_modified"}
    if type(obj) is not dict or set(obj)!=exact or obj["schema"]!=SCHEMA:
        raise StageAuditError("STAGE_RECEIPT_SCHEMA_INVALID")
    if (type(obj["plan_sha256"]) is not str or
        not SAFE_SHA.fullmatch(obj["plan_sha256"])):
        raise StageAuditError("STAGE_PLAN_DIGEST_INVALID")
    if obj["release_approved"] is not False or obj["live_mcp_modified"] is not False:
        raise StageAuditError("STAGE_RECEIPT_CLAIMS_PRIVILEGE")
    sids=[obj[k] for k in ("service_sid","reviewer_sid","worker_sid")]
    if any(type(x) is not str or not SAFE_SID.fullmatch(x) for x in sids):
        raise StageAuditError("STAGE_SID_INVALID")
    if len(set(sids))!=3 or SYSTEM_SID in sids:
        raise StageAuditError("STAGE_SID_NOT_ISOLATED")
    entries=obj["files"]
    if type(entries) is not list or len(entries)!=len(EXPECTED_FILES):
        raise StageAuditError("STAGE_FILE_SET_INVALID")
    names=set()
    for entry in entries:
        if (type(entry) is not dict or set(entry)!={"relative","sha256"} or
                type(entry["relative"]) is not str or
                type(entry["sha256"]) is not str or
                not SAFE_SHA.fullmatch(entry["sha256"])):
            raise StageAuditError("STAGE_FILE_ENTRY_INVALID")
        rel=entry["relative"].replace("\\","/")
        if rel in names or rel not in EXPECTED_FILES:
            raise StageAuditError("STAGE_FILE_SET_INVALID")
        names.add(rel)
    if names!=EXPECTED_FILES:
        raise StageAuditError("STAGE_FILE_SET_INVALID")
    return obj

def _acl_sane(path:Path,service_sid:str,*,mutable:bool,reader)->None:
    try:
        record=reader(path)
        owner=record["owner_sid"]
        writers=record["write_allowed_sids"]
        readable=record["read_execute_allowed_sids"]
        deny_present=record["has_deny_aces"]
        interpretation=record["acl_interpretation"]
    except (WindowsIsolationError,ValueError,OSError,KeyError,TypeError):
        raise StageAuditError("STAGE_ACL_READ_FAILED") from None
    if deny_present is not False or interpretation!="CONSERVATIVE_BASIC_ACES_ONLY":
        raise StageAuditError("STAGE_ACL_UNSUPPORTED_OR_DENY_ACE")
    if owner!=SYSTEM_SID or type(writers) is not list:
        raise StageAuditError("STAGE_OWNER_UNTRUSTED")
    if type(readable) is not list or any(type(x) is not str for x in readable):
        raise StageAuditError("STAGE_ACL_READ_EXECUTE_UNKNOWN")
    allowed={SYSTEM_SID,service_sid} if mutable else {SYSTEM_SID}
    if any(type(sid) is not str or sid not in allowed for sid in writers):
        raise StageAuditError("STAGE_ACL_WRITE_SCOPE_VIOLATION")
    if mutable and service_sid not in writers:
        raise StageAuditError("STAGE_PRIVATE_STATE_SERVICE_WRITE_MISSING")
    # File/directory DACLs must include an explicit service Read+Execute
    # allow ACE; otherwise the separated service cannot read staged code.
    # Effective access still requires a real distinct-SID E2E.
    if service_sid not in readable:
        raise StageAuditError("STAGE_SERVICE_READ_EXECUTE_ACE_MISSING")

def _within_programdata(stage:Path,programdata:Path)->bool:
    # Strictly one stage directory directly below ProgramData. It must not
    # follow a user-controlled junction from any ancestor.
    if stage.parent!=programdata or stage.name.startswith("."):
        return False
    if programdata.is_symlink() or getattr(programdata,"is_junction",lambda:False)():
        return False
    return True

def inspect_staged_package(
    stage:Path, *, programdata:Path|None=None,
    acl_reader=inspect_acl, expected_plan_sha256:str|None=None,
)->dict:
    """Return redacted, non-authorizing status. Does not read private_state."""
    from collections import Counter
    default_root=Path(os.environ.get("ProgramData","C:/ProgramData"))
    programdata=default_root if programdata is None else Path(programdata)
    stage=Path(stage)
    blockers=[]
    source_count=0
    plan_matches=None
    if (not _within_programdata(stage,programdata)
        or stage.is_symlink()
        or getattr(stage,"is_junction",lambda:False)()):
        return {"status":"UNTRUSTED_LOCATION","file_count":0,
                "blockers":["STAGE_LOCATION_OUTSIDE_PROGRAMDATA"],
                "trusted_deployment_verified":False,"auto_merge_authorized":False}
    if not stage.exists():
        return {"status":"NOT_STAGED","file_count":0,
                "blockers":["P085_STAGE_NOT_INSTALLED"],
                "trusted_deployment_verified":False,"auto_merge_authorized":False}
    try:
        if not _directory(stage):
            raise StageAuditError("STAGE_ROOT_INVALID")
        receipt_file=stage/"STAGE_RECEIPT.json"
        if not _regular(receipt_file) or receipt_file.stat().st_size>MAX_RECEIPT_BYTES:
            raise StageAuditError("STAGE_RECEIPT_MISSING")
        raw=receipt_file.read_bytes()
        if len(raw)>MAX_RECEIPT_BYTES:
            raise StageAuditError("STAGE_RECEIPT_INVALID")
        receipt=_receipt(raw)
        if expected_plan_sha256 is not None:
            if (type(expected_plan_sha256) is not str or
                    not SAFE_SHA.fullmatch(expected_plan_sha256)):
                raise StageAuditError("EXPECTED_PLAN_DIGEST_INVALID")
            plan_matches=receipt["plan_sha256"]==expected_plan_sha256
            if not plan_matches:
                raise StageAuditError("STAGE_PLAN_DIGEST_MISMATCH")
        paths=(stage/"code",stage/"private_state",
               stage/"code"/"workspace_mcp",stage/"code"/"p085_trusted")
        if any(not _directory(p) for p in paths):
            raise StageAuditError("STAGE_DIRECTORY_INVALID")
        if {p.name for p in stage.iterdir()}!=ROOT_ITEMS:
            raise StageAuditError("STAGE_UNEXPECTED_ROOT_ENTRY")
        if {p.name for p in (stage/"code").iterdir()}!={
                "workspace_mcp","p085_trusted","p085_reviewer_service.py"}:
            raise StageAuditError("STAGE_UNEXPECTED_CODE_ENTRY")
        expected={e["relative"].replace("\\","/"):e["sha256"]
                  for e in receipt["files"]}
        for folder in (stage/"code"/"workspace_mcp",
                       stage/"code"/"p085_trusted"):
            actual={p.name for p in folder.iterdir()}
            want={Path(x).name for x in EXPECTED_FILES
                  if str(Path(x).parent).replace("\\","/")==
                  str(folder.relative_to(stage)).replace("\\","/")}
            if actual!=want:
                raise StageAuditError("STAGE_UNEXPECTED_MODULE")
        for directory in (stage,stage/"code",stage/"code"/"workspace_mcp",
                          stage/"code"/"p085_trusted"):
            _acl_sane(directory,receipt["service_sid"],
                      mutable=False,reader=acl_reader)
        _acl_sane(stage/"private_state",receipt["service_sid"],
                  mutable=True,reader=acl_reader)
        _acl_sane(receipt_file,receipt["service_sid"],
                  mutable=False,reader=acl_reader)
        for name,digest in expected.items():
            file=stage.joinpath(*name.split("/"))
            if not _regular(file) or file.stat().st_size>MAX_PY_BYTES:
                raise StageAuditError("STAGE_CODE_FILE_INVALID")
            data=file.read_bytes()
            if len(data)>MAX_PY_BYTES or hashlib.sha256(data).hexdigest()!=digest:
                raise StageAuditError("STAGE_SOURCE_SHA_MISMATCH")
            _acl_sane(file,receipt["service_sid"],
                      mutable=False,reader=acl_reader)
            source_count+=1
        status="STRUCTURE_AND_ACL_OBSERVED"
        # No private-state listing or secret bytes; even a green audit is
        # NOT a signed owner release or proof of OS token separation.
    except (StageAuditError,OSError) as exc:
        status="BLOCKED"
        code=str(exc) if type(exc) is StageAuditError else "STAGE_IO_UNAVAILABLE"
        blockers=[code]
    return {"status":status,"file_count":source_count,
            "plan_digest_match":plan_matches,
            "blockers":blockers,
            "trusted_deployment_verified":False,
            "auto_merge_authorized":False}

def main():
    stage=Path(os.environ.get("ProgramData","C:/ProgramData"))/"P085ProtectedReviewer"
    print(json.dumps({"ok":True,"stage":inspect_staged_package(stage)},
                     sort_keys=True,ensure_ascii=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
