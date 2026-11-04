"""Windows-only read-only OS boundary inspection (NOT an ACL installer).

Inspects actual Windows token SID and file DACLs with Win32 APIs. A broker
sharing the agent account, or any protected path permitting writes by a
non-broker/non-SYSTEM SID, fails closed. No chmod, icacls, service install,
UAC escalation, key management or live MCP change is attempted.

Limitations: This does NOT authenticate the caller who supplies SIDs, and
cannot defend against local Administrators, SeBackupPrivilege, untrusted
launcher/executable edits, reparse-point races or compromises of the trusted
Windows kernel. It is a necessary preflight check, not proof of isolation.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import sys
from typing import Iterable

SID = re.compile(r"^S-1-[0-9]+(?:-[0-9]+){1,16}$")
SYSTEM_SID="S-1-5-18"
# Generic-write/all; explicit file/directory mutation; DELETE/WRITE_DAC/
# WRITE_OWNER. Fail closed when any unexpected grant to other principals.
WRITABLE_MASK=(0x40000000|0x10000000|0x00010000|0x00040000|
               0x00080000|0x0002|0x0004|0x0010|0x0040|0x0100)
# For immutable code, verify the service has an explicit allow ACE granting
# BOTH file read/list (0x1) and execute/traverse (0x20). These are
# conservative evidence, not an effective-access test across nested groups.
READ_EXECUTE_MASK=0x00000001|0x00000020
MAX_ACE=512

class WindowsIsolationError(ValueError):
    pass


def _windows():
    if sys.platform!="win32":
        raise WindowsIsolationError("WINDOWS_ISOLATION_UNAVAILABLE")


def _win32():
    _windows()
    from ctypes import wintypes as wt
    adv=ctypes.WinDLL("advapi32",use_last_error=True)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    adv.OpenProcessToken.argtypes=[wt.HANDLE,wt.DWORD,ctypes.POINTER(wt.HANDLE)]
    adv.OpenProcessToken.restype=wt.BOOL
    adv.GetTokenInformation.argtypes=[
        wt.HANDLE,wt.DWORD,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD)]
    adv.GetTokenInformation.restype=wt.BOOL
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSidToStringSidW.restype=wt.BOOL
    adv.GetNamedSecurityInfoW.argtypes=[
        wt.LPWSTR,wt.DWORD,wt.DWORD,ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype=wt.DWORD
    adv.GetAclInformation.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wt.DWORD,wt.DWORD]
    adv.GetAclInformation.restype=wt.BOOL
    adv.GetAce.argtypes=[ctypes.c_void_p,wt.DWORD,ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.restype=wt.BOOL
    adv.GetLengthSid.argtypes=[ctypes.c_void_p]
    adv.GetLengthSid.restype=wt.DWORD
    adv.IsValidSid.argtypes=[ctypes.c_void_p]
    adv.IsValidSid.restype=wt.BOOL
    kernel.GetCurrentProcess.restype=wt.HANDLE
    kernel.CloseHandle.argtypes=[wt.HANDLE]
    kernel.CloseHandle.restype=wt.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    return adv,kernel,wt


def _sid_string(pointer,adv,kernel) -> str:
    if not pointer or not adv.IsValidSid(pointer):
        raise WindowsIsolationError("WINDOWS_SID_INVALID")
    out=ctypes.c_void_p()
    if not adv.ConvertSidToStringSidW(pointer,ctypes.byref(out)):
        raise WindowsIsolationError("WINDOWS_SID_CONVERSION_FAILED")
    try:
        text=ctypes.wstring_at(out.value)
    finally:
        kernel.LocalFree(out)
    if not SID.fullmatch(text):
        raise WindowsIsolationError("WINDOWS_SID_FORMAT_INVALID")
    return text


def current_user_sid() -> str:
    """Return current PROCESS identity, not the user name or claimed role."""
    adv,kernel,wt=_win32()
    handle=wt.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),0x0008,
                                ctypes.byref(handle)):
        raise WindowsIsolationError("WINDOWS_TOKEN_QUERY_DENIED")
    try:
        needed=wt.DWORD(0)
        adv.GetTokenInformation(handle,1,None,0,ctypes.byref(needed))
        if not 16 <= needed.value <= 65536:
            raise WindowsIsolationError("WINDOWS_TOKEN_SIZE_INVALID")
        buffer=ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(handle,1,buffer,needed,
                                       ctypes.byref(needed)):
            raise WindowsIsolationError("WINDOWS_TOKEN_READ_FAILED")
        sid_ptr=ctypes.c_void_p.from_buffer(buffer).value
        return _sid_string(sid_ptr,adv,kernel)
    finally:
        kernel.CloseHandle(handle)


def inspect_acl(path: str | Path) -> dict:
    """Read the ACTUAL owner SID and allow ACEs; never changes permissions."""
    adv,kernel,wt=_win32()
    p=Path(path)
    if p.is_symlink() or getattr(p,"is_junction",lambda:False)():
        raise WindowsIsolationError("WINDOWS_REPARSE_POINT_DENIED")
    if not p.exists():
        raise WindowsIsolationError("WINDOWS_PROTECTED_PATH_MISSING")
    p=p.resolve(strict=True)
    owner=ctypes.c_void_p()
    dacl=ctypes.c_void_p()
    desc=ctypes.c_void_p()
    # SE_FILE_OBJECT=1; OWNER_SECURITY_INFORMATION=1; DACL...=4.
    status=adv.GetNamedSecurityInfoW(
        str(p),1,0x00000005,ctypes.byref(owner),None,
        ctypes.byref(dacl),None,ctypes.byref(desc))
    if status!=0:
        raise WindowsIsolationError("WINDOWS_DACL_READ_DENIED")
    try:
        if not dacl.value:
            raise WindowsIsolationError("WINDOWS_NULL_DACL_DENIED")
        owner_sid=_sid_string(owner,adv,kernel)
        class ACLSize(ctypes.Structure):
            _fields_=[
                ("AceCount",wt.DWORD),
                ("AclBytesInUse",wt.DWORD),
                ("AclBytesFree",wt.DWORD),
            ]
        size=ACLSize()
        if not adv.GetAclInformation(dacl,ctypes.byref(size),
                                     ctypes.sizeof(size),2):
            raise WindowsIsolationError("WINDOWS_ACL_INFO_FAILED")
        if size.AceCount>MAX_ACE:
            raise WindowsIsolationError("WINDOWS_ACL_TOO_MANY_ENTRIES")
        writers=set()
        read_execute_bits={}
        has_deny_aces=False
        for index in range(size.AceCount):
            ace=ctypes.c_void_p()
            if not adv.GetAce(dacl,index,ctypes.byref(ace)):
                raise WindowsIsolationError("WINDOWS_ACE_READ_FAILED")
            if not ace.value:
                raise WindowsIsolationError("WINDOWS_ACE_NULL")
            header=ctypes.string_at(ace.value,4)
            kind=header[0]
            length=int.from_bytes(header[2:4],"little")
            if length<8 or length>65535:
                raise WindowsIsolationError("WINDOWS_ACE_SIZE_INVALID")
            # Only basic ACCESS_ALLOWED_ACE / ACCESS_DENIED_ACE understood.
            # Object/conditional/callback/mandatory-label ACEs are not safe
            # to approximate as if absent, so fail closed on such ACLs.
            if kind not in (0,1):
                raise WindowsIsolationError("WINDOWS_ACE_TYPE_UNSUPPORTED")
            if kind!=0:
                # Deny ACEs may apply through group membership that this
                # reader cannot resolve. A staged service with ANY deny ACE
                # is not eligible for positive ACL-health classification.
                has_deny_aces=True
                continue
            mask=int.from_bytes(ctypes.string_at(ace.value+4,4),"little")
            if mask & (WRITABLE_MASK|READ_EXECUTE_MASK|0xB0000000):
                sid_ptr=ctypes.c_void_p(ace.value+8)
                sid_len=adv.GetLengthSid(sid_ptr)
                if not sid_len or sid_len>length-8:
                    raise WindowsIsolationError("WINDOWS_ACE_SID_INVALID")
                sid=_sid_string(sid_ptr,adv,kernel)
                if mask & WRITABLE_MASK:
                    writers.add(sid)
                # A generic access flag itself grants the corresponding
                # right. Accumulate split read and execute allow ACEs.
                granted=mask & READ_EXECUTE_MASK
                if mask & (0x80000000|0x10000000):
                    granted|=0x00000001
                if mask & (0x20000000|0x10000000):
                    granted|=0x00000020
                read_execute_bits[sid]=read_execute_bits.get(sid,0)|granted
        return {
            "owner_sid":owner_sid,
            "write_allowed_sids":sorted(writers),
            "read_execute_allowed_sids":sorted(
                sid for sid,bits in read_execute_bits.items()
                if bits & READ_EXECUTE_MASK==READ_EXECUTE_MASK),
            "has_deny_aces":has_deny_aces,
            "acl_ace_count":size.AceCount,
            "is_directory":p.is_dir(),
            "acl_interpretation":"CONSERVATIVE_BASIC_ACES_ONLY",
        }
    finally:
        kernel.LocalFree(desc)


def preflight_isolation(
    *, broker_sid: str, agent_sid: str,
    protected_paths: Iterable[str | Path],
    immutable_code_paths: Iterable[str | Path] = (),
) -> dict:
    """Run a strict read-only gate; false is the only safe result on doubt.

    Input SIDs MUST be pinned by a separate trusted OS configuration; client
    input is not an authorization source. Caller protects the launcher too.
    """
    findings=[]
    if not isinstance(broker_sid,str) or not SID.fullmatch(broker_sid):
        findings.append("BROKER_SID_NOT_PINNED")
    if not isinstance(agent_sid,str) or not SID.fullmatch(agent_sid):
        findings.append("AGENT_SID_NOT_PINNED")
    actual=None
    try:
        actual=current_user_sid()
    except (WindowsIsolationError,OSError) as exc:
        findings.append(str(exc)[:90])
    if actual and actual!=broker_sid:
        findings.append("BROKER_PROCESS_TOKEN_MISMATCH")
    if broker_sid==agent_sid:
        findings.append("BROKER_AND_AGENT_SHARE_SID")
    if isinstance(protected_paths,(str,Path)) or protected_paths is None:
        findings.append("PROTECTED_PATHS_INVALID")
        paths=[]
    else:
        paths=list(protected_paths)
    if not 2<=len(paths)<=64:
        findings.append("PROTECTED_PATHS_INCOMPLETE")
    # Distinct paths and their parent directories must be protected.
    seen=set()
    for item in paths:
        try:
            p=Path(item)
            if p.is_symlink() or getattr(p,"is_junction",lambda:False)():
                raise WindowsIsolationError("WINDOWS_REPARSE_POINT_DENIED")
            for candidate in (p,p.parent):
                canonical=str(candidate.absolute()).casefold()
                if canonical in seen:
                    continue
                seen.add(canonical)
                acl=inspect_acl(candidate)
                if acl["owner_sid"] not in (broker_sid,SYSTEM_SID):
                    findings.append("UNTRUSTED_PROTECTED_PATH_OWNER")
                for sid in acl["write_allowed_sids"]:
                    if sid not in (broker_sid,SYSTEM_SID):
                        findings.append("UNTRUSTED_WRITE_ACE")
                        break
        except (WindowsIsolationError,OSError,ValueError) as exc:
            findings.append(str(exc)[:90])

    # A broker may write its private journal but MUST NOT be able to modify
    # the Python runtime or executable code it is asked to trust. Inspect
    # immutable code paths and their parent directories separately from
    # the mutable state paths. This check is intentionally stricter than the
    # general broker/SYSTEM-only write ACL gate.
    code_findings=[]
    if isinstance(immutable_code_paths,(str,Path)) or immutable_code_paths is None:
        code_findings.append("IMMUTABLE_CODE_PATHS_INVALID")
        code_paths=[]
    else:
        try:
            code_paths=list(immutable_code_paths)
        except (TypeError,ValueError):
            code_findings.append("IMMUTABLE_CODE_PATHS_INVALID")
            code_paths=[]
    if len(code_paths)>64:
        code_findings.append("IMMUTABLE_CODE_PATHS_LIMIT")
        code_paths=[]
    included={str(Path(value).absolute()).casefold()
              for value in paths if isinstance(value,(str,Path))}
    for candidate in code_paths:
        if not isinstance(candidate,(str,Path)):
            code_findings.append("IMMUTABLE_CODE_PATH_INVALID")
            continue
        try:
            code=Path(candidate)
            if str(code.absolute()).casefold() not in included:
                code_findings.append("IMMUTABLE_CODE_NOT_PROTECTED")
            for target in (code,code.parent):
                info=inspect_acl(target)
                if info["owner_sid"]!=SYSTEM_SID:
                    code_findings.append("IMMUTABLE_CODE_OWNER_UNTRUSTED")
                if any(sid!=SYSTEM_SID for sid in info["write_allowed_sids"]):
                    code_findings.append("IMMUTABLE_CODE_MUTABLE_BY_SERVICE_OR_AGENT")
        except (WindowsIsolationError,OSError,ValueError) as exc:
            code_findings.append(str(exc)[:90])
    findings.extend(code_findings)
    return {
        "immutable_code_acl_verified":bool(code_paths and not code_findings),
        "filesystem_preflight_passed":not findings,
        "ready_for_production":False,
        "findings":sorted(set(findings)),
        "broker_token_matches_pin":bool(actual and actual==broker_sid),
        "broker_and_agent_distinct":bool(broker_sid!=agent_sid),
        "acl_paths_inspected":len(seen),
        "windows_only":True,
        "read_only":True,
        "ipc_parent_authenticated":False,
        "windows_admin_bypass_ruled_out":False,
        "live_mcp_privileged_tools_removed":False,
        "production_deployment_authorized":False,
    }
