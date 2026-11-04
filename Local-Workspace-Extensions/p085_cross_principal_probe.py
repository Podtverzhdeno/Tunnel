"""P085 read-only cross-principal Windows access evidence.

Run this as EACH REAL Windows principal under a genuine local console (worker,
reviewer, QA child), never interpret a model's role string as identity.
Never reads signing key contents, alters files/ACLs, or mints approval.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path

from p085_windows_isolation_probe import assessment, ERROR_ACCESS_DENIED
from ctypes import wintypes as w

# Read-only diagnostic only; never use an editable project module as
# privileged authorization evidence. The production broker has its own
# protected, pinned host_pipe_peer.py implementation.
HOST_SRC = Path(__file__).resolve().parent / "mcp-host-source"
if str(HOST_SRC) not in sys.path:
    sys.path.insert(0,str(HOST_SRC))
from workspace_mcp.host_pipe_peer import current_process_sid, SID

FILE_READ_DATA = 0x0001
FILE_SHARE_ALL = 0x00000007
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value


def probe_key_read(path: Path) -> dict:
    if os.name!="nt":
        return {"state":"OS_UNSUPPORTED","key_read_denied":False}
    path=Path(path)
    # Do not call is_file() before CreateFileW: a properly ACL-protected
    # private directory may make is_file() return False under worker SID.
    # That would incorrectly report MISSING rather than ACCESS_DENIED.
    try:
        if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
            return {"state":"LINKED_PRIVATE_KEY_INCONCLUSIVE","key_read_denied":False}
    except OSError:
        pass
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,
                     w.DWORD,w.DWORD,w.HANDLE]
    create.restype=w.HANDLE
    close=kernel.CloseHandle
    close.argtypes=[w.HANDLE]
    close.restype=w.BOOL
    ctypes.set_last_error(0)
    h=create(str(path),FILE_READ_DATA,FILE_SHARE_ALL,None,
             OPEN_EXISTING,0,None)
    if h is not None and h!=INVALID_HANDLE:
        close(h)
        return {"state":"KEY_READ_ALLOWED","key_read_denied":False}
    code=ctypes.get_last_error()
    if code==ERROR_ACCESS_DENIED:
        return {"state":"KEY_ACCESS_DENIED","key_read_denied":True}
    if code in (2,3):
        return {"state":"MISSING_PRIVATE_KEY","key_read_denied":False}
    return {"state":"KEY_ACCESS_INCONCLUSIVE","key_read_denied":False}


def report(project: Path, protected_install: Path,
           expected_worker_sid: str | None = None) -> dict:
    from platform import system
    if (expected_worker_sid is not None
            and (type(expected_worker_sid) is not str
                 or not SID.fullmatch(expected_worker_sid))):
        raise ValueError("INVALID_EXPECTED_WORKER_SID")
    p=assessment(project)
    install=Path(protected_install)
    actual_sid=current_process_sid() if os.name=="nt" else None
    matched=(actual_sid is not None and
             expected_worker_sid is not None and
             actual_sid==expected_worker_sid)
    keys={
        "review_key":probe_key_read(install/"p085_private"/"review-key.bin"),
        "qa_verification_key":probe_key_read(
            install/"p085_private"/"qa-verify-key.bin"),
    }
    all_write_denied=p["untrusted_effective_write_denied"]
    all_keys_denied=all(x["key_read_denied"] for x in keys.values())
    return {
        "schema":"p085-cross-principal-readonly-probe-v1",
        "operating_system":system(),
        "actual_process_sid":actual_sid,
        "expected_worker_sid_provided":expected_worker_sid is not None,
        "worker_sid_matches_expected":matched,
        "role_is_os_attested":matched,
        "no_file_content_read":True,
        "no_mutations":True,
        "protected_source_write_denied":all_write_denied,
        "service_key_read_denied":all_keys_denied,
        "untrusted_worker_ready_for_cutover":matched and all_write_denied and all_keys_denied,
        "state_writes":p["paths"],
        "private_key_access":keys,
        "note":"Operator must independently attest the actual OS SID and test named-pipe access under separately logged-on accounts.",
    }


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--protected-install",type=Path,required=True)
    ap.add_argument("--expected-worker-sid",required=True,
                    help="Operator-attested worker SID (not a model role)")
    args=ap.parse_args(argv)
    try:
        result=report(args.project,args.protected_install,
                      expected_worker_sid=args.expected_worker_sid)
    except (OSError,ValueError,RuntimeError):
        print(json.dumps({"ok":False,"error_code":"READONLY_PROBE_UNAVAILABLE"}))
        return 2
    print(json.dumps({"ok":True,"data":result},sort_keys=True))
    return 0 if result["untrusted_worker_ready_for_cutover"] else 3


if __name__=="__main__":
    raise SystemExit(main())
