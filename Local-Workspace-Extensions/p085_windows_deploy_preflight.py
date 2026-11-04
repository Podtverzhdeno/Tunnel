"""Read-only, fail-closed preparation audit for Windows P085 broker isolation.

No OS mutation, user creation, credentials, tunneling or service install.
Outputs only non-sensitive capability state and stable blocker codes.
"""
from __future__ import annotations
import ctypes
import json
import os
import sys
from pathlib import Path
from experiments.foundation_kernel.windows_isolation import (
    current_user_sid, inspect_acl, WindowsIsolationError, SYSTEM_SID,
)

ROOT=Path(__file__).resolve().parent
LOCAL_SERVICE_SID="S-1-5-19"
IMMUTABLE_CODE_WRITERS=frozenset((SYSTEM_SID,))


def os_protected(path:Path, *, trusted=IMMUTABLE_CODE_WRITERS)->bool:
    """Code and interpreter: SYSTEM-only writes, even service cannot edit.

    This differs from private state, which the broker account must write.
    A positive result is conservative evidence, NOT proof of full OS
    protection against administrators/other privileged bypasses.
    """
    if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
        return False
    for item in (path,path.parent):
        try:
            record=inspect_acl(item)
            if (record.get("owner_sid") not in trusted or
                any(writer not in trusted for writer
                    in record.get("write_allowed_sids",[]))):
                return False
        except (OSError,ValueError,WindowsIsolationError):
            return False
    return True


def inspect():
    blockers=[]
    if sys.platform!="win32":
        blockers.append("WINDOWS_REQUIRED")
        admin=False
        sid=False
    else:
        try: admin=bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (OSError,AttributeError): admin=False
        try: sid=bool(current_user_sid())
        except (WindowsIsolationError,OSError): sid=False
    if not admin: blockers.append("ELEVATED_INSTALLER_NOT_AVAILABLE")
    if not sid: blockers.append("WINDOWS_PROCESS_SID_NOT_VERIFIED")
    # The interpreter is executable code: a broker with an agent-owned
    # python.exe can be hijacked before its signed-policy checks execute.
    interpreter_trusted=(sys.platform=="win32"
                         and os_protected(Path(sys.executable)))
    broker_source_trusted=(sys.platform=="win32"
                           and os_protected(ROOT/"experiments"/
                               "foundation_kernel"/"broker_named_pipe.py"))
    if not interpreter_trusted:
        blockers.append("BROKER_PYTHON_RUNTIME_UNTRUSTED")
    if not broker_source_trusted:
        blockers.append("BROKER_CODE_TREE_UNTRUSTED")
    required=[
        ROOT/"experiments"/"foundation_kernel"/"broker_supervisor.py",
        ROOT/"experiments"/"foundation_kernel"/"broker_named_pipe.py",
        ROOT/"experiments"/"foundation_kernel"/"signed_policy.py",
        ROOT/"experiments"/"foundation_kernel"/"windows_isolation.py",
    ]
    missing=[p.name for p in required if not p.is_file() or p.is_symlink()]
    if missing: blockers.append("BROKER_SOURCE_INCOMPLETE")
    from p085_stage_audit import inspect_staged_package
    programdata=Path(os.environ.get("ProgramData","C:/ProgramData"))
    stage=inspect_staged_package(
        programdata/"P085ProtectedReviewer",programdata=programdata)
    if stage["status"] not in ("NOT_STAGED","STRUCTURE_AND_ACL_OBSERVED"):
        blockers.append("P085_STAGED_CODE_INTEGRITY_UNVERIFIED")
    return {"ok":True,"read_only":True,"windows":sys.platform=="win32",
            "current_process_elevated":admin,"current_process_sid_verified":sid,
            "python_runtime_os_protected":interpreter_trusted,
            "broker_code_tree_os_protected":broker_source_trusted,
            "source_files_present":len(required)-len(missing),
            "missing_source_modules":missing,
            "separate_broker_process_verified":False,
            "separate_agent_process_verified":False,
            "credentials_or_grants_provisioned":False,
            "service_installed":False,
            "stage_health":stage,
            "safe_to_activate":False,
            "blockers":blockers+["BROKER_SERVICE_UNPROVISIONED","REAL_TWO_SID_E2E_UNVERIFIED"]}

if __name__=="__main__":
    print(json.dumps(inspect(),sort_keys=True,ensure_ascii=True))
