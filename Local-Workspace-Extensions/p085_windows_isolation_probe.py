"""P085 Windows *effective-access* isolation probe (read-only, fail closed).

This is an INSTRUMENT, not an authorization token, ACL installer or proof
that a privileged Windows reviewer service exists. Run it under the exact OS
principal used by the untrusted, scheduled-agent MCP process. Never rely on
a caller-supplied --role, Windows username text, or HTTP tunnel metadata.

Only attempts CreateFileW(..., FILE_WRITE_DATA or FILE_ADD_FILE, OPEN_EXISTING)
and closes the handle; NEVER writes bytes, alters ACLs or creates files.
A sharing violation, read-only attribute or other ambiguous error is UNKNOWN,
not an ACL success. A separate reviewer process + ACLs remain mandatory.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
FILE_WRITE_DATA = 0x0002
FILE_ADD_FILE = 0x0002
FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

def probe(path: Path) -> dict:
    """Result is intentionally limited to safe status codes and path label."""
    if os.name != "nt":
        return {"state": "OS_UNSUPPORTED", "write_denied": False}
    path = Path(path)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return {"state": "LINKED_PATH_INCONCLUSIVE", "write_denied": False}
    if not path.exists():
        return {"state": "MISSING_PATH_INCONCLUSIVE", "write_denied": False}
    if not path.is_file() and not path.is_dir():
        return {"state": "NONREGULAR_PATH_INCONCLUSIVE", "write_denied": False}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attrs = kernel.GetFileAttributesW
    get_attrs.argtypes = [ctypes.c_wchar_p]
    get_attrs.restype = ctypes.c_uint32
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    filename = str(path.resolve(strict=True))
    attr = get_attrs(filename)
    if attr == INVALID_FILE_ATTRIBUTES:
        return {"state": "ATTRIBUTES_UNAVAILABLE", "write_denied": False}
    if attr & FILE_ATTRIBUTE_READONLY:
        # A DOS read-only bit can deny write independently of an OS ACL.
        # It does not establish account separation.
        return {"state": "READONLY_ATTRIBUTE_INCONCLUSIVE", "write_denied": False}
    is_dir = bool(attr & FILE_ATTRIBUTE_DIRECTORY)
    flags = FILE_FLAG_BACKUP_SEMANTICS if is_dir else 0
    ctypes.set_last_error(0)
    fd = create(filename, FILE_ADD_FILE if is_dir else FILE_WRITE_DATA,
                FILE_SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if fd is not None and fd != INVALID_HANDLE_VALUE:
        close(fd)
        return {"state": "UNTRUSTED_WRITE_ALLOWED", "write_denied": False}
    code = ctypes.get_last_error()
    if code == ERROR_ACCESS_DENIED:
        return {"state": "EFFECTIVE_WRITE_ACCESS_DENIED", "write_denied": True}
    if code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
        return {"state": "SHARING_OR_LOCK_INCONCLUSIVE", "write_denied": False}
    return {"state": "OS_ERROR_INCONCLUSIVE", "write_denied": False}

def assessment(project: Path) -> dict:
    project = Path(project).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("PROJECT_ROOT_INVALID")
    targets = {
        "coordinator_db": project / ".agent-team" / "team.sqlite3",
        "coordinator_dir": project / ".agent-team",
        "canonical_source": project / "agent_team.py",
        "canonical_project_dir": project,
    }
    results = {name: probe(path) for name, path in targets.items()}
    isolated = all(item["write_denied"] for item in results.values())
    return {
        "schema": "p085-windows-effective-access-probe-v1",
        "read_only": True,
        "caller_is_untrusted_principal": "ASSUMED_NOT_ATTESTED",
        "untrusted_effective_write_denied": isolated,
        "isolation_verified": False,  # this is NOT a second-principal ACL E2E
        "ready_for_privileged_review": False,
        "paths": results,
        "next": ("Run equivalent ACL tests from a genuine distinct reviewer OS"
                 " principal, attest peer identities and restrict legacy MCP"
                 " before enabling any acceptance endpoint."),
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    try:
        report = assessment(args.project)
        print(json.dumps({"ok": True, "data": report}, sort_keys=True))
        return 0 if report["untrusted_effective_write_denied"] else 3
    except (ValueError, OSError, RuntimeError):
        print(json.dumps({"ok": False, "error_code": "PROBE_UNAVAILABLE"}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
