"""Windows named-pipe IPC reference with kernel-verified client SID.

EXPERIMENT ONLY. The actual MCP server and scheduler are not modified.
The server creates a first-instance, local-only pipe with a protected DACL
and no Everyone/Administrators ACE. After reading a bounded message it
impersonates that message's client, reads the *actual* thread token SID,
reverts immediately, and compares it to an externally pinned SID.

This does NOT provision a separate OS account, protect startup arguments,
authenticate an admin-controlled launcher, or remove privileged MCP tools.
A same-SID fixture is useful for IPC functional tests but NOT OS isolation.

Windows Win32 API rules: ImpersonateNamedPipeClient uses the last *read*
message; it MUST be checked, and RevertToSelf MUST be called.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

try:
    from .win_pipe_overlapped import (
        FILE_FLAG_OVERLAPPED,TimedPipeError,
        connect as timed_connect,read as timed_read,write as timed_write)
except ImportError:
    from win_pipe_overlapped import (
        FILE_FLAG_OVERLAPPED,TimedPipeError,
        connect as timed_connect,read as timed_read,write as timed_write)

MAX_FRAME = 16 * 1024
PIPE_PREFIX = r"\\.\pipe\foundation-broker-"
SAFE_NAME = re.compile(r"^[a-f0-9]{24}$")
SID_PATTERN = re.compile(r"^S-1-[0-9]+(?:-[0-9]+){1,16}$")
INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_PIPE_CONNECTED = 535
ERROR_MORE_DATA = 234

class PipeAccessError(ValueError):
    pass

class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wt.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wt.BOOL),
    ]

def _require_windows():
    if sys.platform != "win32":
        raise PipeAccessError("PIPE_WINDOWS_ONLY")

def _name(suffix: str) -> str:
    if type(suffix) is not str or not SAFE_NAME.fullmatch(suffix):
        raise PipeAccessError("PIPE_NAME_INVALID")
    return PIPE_PREFIX + suffix

def _validate_sid(sid: str, label: str):
    if type(sid) is not str or not SID_PATTERN.fullmatch(sid):
        raise PipeAccessError(label + "_INVALID")

def _kernel():
    _require_windows()
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateNamedPipeW.argtypes=[
        wt.LPCWSTR,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,
        wt.DWORD,ctypes.POINTER(SECURITY_ATTRIBUTES),
    ]
    dll.CreateNamedPipeW.restype=wt.HANDLE
    dll.ConnectNamedPipe.argtypes=[wt.HANDLE,ctypes.c_void_p]
    dll.ConnectNamedPipe.restype=wt.BOOL
    dll.DisconnectNamedPipe.argtypes=[wt.HANDLE]
    dll.DisconnectNamedPipe.restype=wt.BOOL
    dll.ReadFile.argtypes=[
        wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p]
    dll.ReadFile.restype=wt.BOOL
    dll.WriteFile.argtypes=[
        wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p]
    dll.WriteFile.restype=wt.BOOL
    dll.CloseHandle.argtypes=[wt.HANDLE]
    dll.CloseHandle.restype=wt.BOOL
    dll.GetCurrentThread.restype=wt.HANDLE
    dll.GetNamedPipeServerProcessId.argtypes=[
        wt.HANDLE,ctypes.POINTER(wt.ULONG)]
    dll.GetNamedPipeServerProcessId.restype=wt.BOOL
    dll.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD]
    dll.OpenProcess.restype=wt.HANDLE
    dll.LocalFree.argtypes=[ctypes.c_void_p]
    dll.LocalFree.restype=ctypes.c_void_p
    dll.CreateFileW.argtypes=[
        wt.LPCWSTR,wt.DWORD,wt.DWORD,ctypes.c_void_p,wt.DWORD,wt.DWORD,wt.HANDLE]
    dll.CreateFileW.restype=wt.HANDLE
    dll.WaitNamedPipeW.argtypes=[wt.LPCWSTR,wt.DWORD]
    dll.WaitNamedPipeW.restype=wt.BOOL
    return dll

def _advapi():
    _require_windows()
    dll=ctypes.WinDLL("advapi32",use_last_error=True)
    dll.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[
        wt.LPCWSTR,wt.DWORD,ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wt.DWORD)]
    dll.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=wt.BOOL
    dll.ImpersonateNamedPipeClient.argtypes=[wt.HANDLE]
    dll.ImpersonateNamedPipeClient.restype=wt.BOOL
    dll.RevertToSelf.argtypes=[]
    dll.RevertToSelf.restype=wt.BOOL
    dll.OpenProcessToken.argtypes=[
        wt.HANDLE,wt.DWORD,ctypes.POINTER(wt.HANDLE)]
    dll.OpenProcessToken.restype=wt.BOOL
    dll.OpenThreadToken.argtypes=[
        wt.HANDLE,wt.DWORD,wt.BOOL,ctypes.POINTER(wt.HANDLE)]
    dll.OpenThreadToken.restype=wt.BOOL
    dll.GetTokenInformation.argtypes=[
        wt.HANDLE,wt.DWORD,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD)]
    dll.GetTokenInformation.restype=wt.BOOL
    dll.ConvertSidToStringSidW.argtypes=[
        ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    dll.ConvertSidToStringSidW.restype=wt.BOOL
    dll.IsValidSid.argtypes=[ctypes.c_void_p]
    dll.IsValidSid.restype=wt.BOOL
    return dll

def security_sddl(broker_sid: str, client_sid: str) -> str:
    """Protected DACL with only SYSTEM, broker and one allowed client SID."""
    _validate_sid(broker_sid,"BROKER_SID")
    _validate_sid(client_sid,"CLIENT_SID")
    # GA/GR/GW are explicit SDDL rights; never use an inherited/default DACL.
    return ("D:P(A;;GA;;;SY)"
            f"(A;;GA;;;{broker_sid})"
            f"(A;;GRGW;;;{client_sid})")

def _sid_text(sid_pointer, adv, kernel):
    if not sid_pointer or not adv.IsValidSid(sid_pointer):
        raise PipeAccessError("PIPE_PEER_SID_INVALID")
    dest=ctypes.c_void_p()
    if not adv.ConvertSidToStringSidW(sid_pointer,ctypes.byref(dest)):
        raise PipeAccessError("PIPE_PEER_SID_CONVERSION_FAILED")
    try:
        result=ctypes.wstring_at(dest.value)
    finally:
        kernel.LocalFree(dest)
    _validate_sid(result,"PEER_SID")
    return result

def peer_sid_after_read(handle, *, kernel=None, adv=None) -> str:
    """Read the caller SID from its OS token after a successful ReadFile.

    Called only from the server end, after a complete message is read.
    Fatal termination on RevertToSelf failure prevents privileged continued
    execution under the wrong token.
    """
    kernel=kernel or _kernel()
    adv=adv or _advapi()
    if not adv.ImpersonateNamedPipeClient(handle):
        raise PipeAccessError("PIPE_PEER_IMPERSONATION_FAILED")
    try:
        token=wt.HANDLE()
        if not adv.OpenThreadToken(kernel.GetCurrentThread(),0x0008,False,
                                   ctypes.byref(token)):
            raise PipeAccessError("PIPE_PEER_TOKEN_OPEN_FAILED")
        try:
            needed=wt.DWORD(0)
            adv.GetTokenInformation(token,1,None,0,ctypes.byref(needed))
            if not 16<=needed.value<=65536:
                raise PipeAccessError("PIPE_PEER_TOKEN_SIZE_INVALID")
            buf=ctypes.create_string_buffer(needed.value)
            if not adv.GetTokenInformation(token,1,buf,needed,
                                           ctypes.byref(needed)):
                raise PipeAccessError("PIPE_PEER_TOKEN_READ_FAILED")
            ptr=ctypes.c_void_p.from_buffer(buf).value
            return _sid_text(ptr,adv,kernel)
        finally:
            kernel.CloseHandle(token)
    finally:
        if not adv.RevertToSelf():
            # Cannot safely continue execution under an impersonated token.
            os._exit(120)

def _write(handle, data: bytes, kernel):
    if type(data) is not bytes or not 1<=len(data)<=MAX_FRAME:
        raise PipeAccessError("PIPE_REPLY_OVERSIZED")
    try:
        return timed_write(kernel,handle,data)
    except TimedPipeError as exc:
        code=str(exc)
        if code=="PIPE_WRITE_RESULT_FAILED":
            code="PIPE_SEND_FAILED"
        raise PipeAccessError(code) from exc

def _read(handle,kernel,*,timeout_ms=1600):
    try:
        return timed_read(kernel,handle,MAX_FRAME,timeout_ms=timeout_ms)
    except TimedPipeError as exc:
        code=str(exc)
        if code=="PIPE_READ_RESULT_FAILED":
            code="PIPE_READ_FAILED"
        raise PipeAccessError(code) from exc

def serve_one(*, suffix: str, broker_sid: str, client_sid: str,
              handler, accept_timeout_ms=3000,
              read_timeout_ms=1600) -> dict:
    """Serve one complete frame to one kernel-authenticated client.

    Always closes the named pipe. Handler receives bytes ONLY when the SID
    is exactly the host-pinned client SID. No response to rejected clients.
    """
    _require_windows()
    if (type(accept_timeout_ms) is not int or not 100<=accept_timeout_ms<=5000
            or type(read_timeout_ms) is not int or not 100<=read_timeout_ms<=5000):
        raise PipeAccessError("PIPE_TIMEOUT_LIMIT_INVALID")
    full_name=_name(suffix)
    sddl=security_sddl(broker_sid,client_sid)
    try:
        from .windows_isolation import current_user_sid
    except ImportError:
        from windows_isolation import current_user_sid
    if current_user_sid()!=broker_sid:
        raise PipeAccessError("PIPE_BROKER_TOKEN_MISMATCH")
    kernel=_kernel()
    adv=_advapi()
    descriptor=ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,1,ctypes.byref(descriptor),None):
        raise PipeAccessError("PIPE_DACL_BUILD_FAILED")
    handle=None
    try:
        attributes=SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES),
                                       descriptor,False)
        handle=kernel.CreateNamedPipeW(
            full_name,
            0x00000003|0x00080000|FILE_FLAG_OVERLAPPED, # DUPLEX + FIRST_INSTANCE + async
            0x00000004|0x00000002|0x00000008, # MESSAGE + READMODE + LOCAL
            1, MAX_FRAME, MAX_FRAME, 3000, ctypes.byref(attributes))
    finally:
        kernel.LocalFree(descriptor)
    if handle is None or handle==INVALID_HANDLE:
        raise PipeAccessError("PIPE_FIRST_INSTANCE_CREATION_FAILED")
    try:
        try:
            timed_connect(kernel,handle,timeout_ms=accept_timeout_ms)
        except TimedPipeError as exc:
            raise PipeAccessError(str(exc)) from exc
        raw=_read(handle,kernel,timeout_ms=read_timeout_ms)
        peer=peer_sid_after_read(handle,kernel=kernel,adv=adv)
        if peer!=client_sid:
            raise PipeAccessError("PIPE_CLIENT_IDENTITY_DENIED")
        if not callable(handler):
            raise PipeAccessError("PIPE_HANDLER_INVALID")
        reply=handler(raw)
        _write(handle,reply,kernel)
        return {"ok":True, "authenticated_peer_sid":peer,
                "os_peer_token_checked":True, "mcp_host_integrated":False,
                "windows_service_isolated":False}
    finally:
        try: kernel.DisconnectNamedPipe(handle)
        finally: kernel.CloseHandle(handle)

def server_process_sid(pipe_handle, *, kernel=None, adv=None) -> str:
    """Identify pipe server through GetNamedPipeServerProcessId + OS token.

    Never trust an unpinned SID supplied in the request payload or an
    unauthenticated reply. Still subject to process replacement TOCTOU;
    production also requires a protected launcher and executable.
    """
    kernel=kernel or _kernel()
    adv=adv or _advapi()
    pid=wt.ULONG(0)
    if not kernel.GetNamedPipeServerProcessId(pipe_handle,ctypes.byref(pid)):
        raise PipeAccessError("PIPE_SERVER_PID_QUERY_DENIED")
    if pid.value<=0:
        raise PipeAccessError("PIPE_SERVER_PID_INVALID")
    process=kernel.OpenProcess(0x1000,False,pid.value)  # QUERY_LIMITED_INFORMATION
    if not process or process==INVALID_HANDLE:
        raise PipeAccessError("PIPE_SERVER_PROCESS_ACCESS_DENIED")
    try:
        token=wt.HANDLE()
        if not adv.OpenProcessToken(process,0x0008,ctypes.byref(token)):
            raise PipeAccessError("PIPE_SERVER_TOKEN_ACCESS_DENIED")
        try:
            needed=wt.DWORD(0)
            adv.GetTokenInformation(token,1,None,0,ctypes.byref(needed))
            if not 16<=needed.value<=65536:
                raise PipeAccessError("PIPE_SERVER_TOKEN_SIZE_INVALID")
            buffer=ctypes.create_string_buffer(needed.value)
            if not adv.GetTokenInformation(token,1,buffer,needed,
                                           ctypes.byref(needed)):
                raise PipeAccessError("PIPE_SERVER_TOKEN_READ_FAILED")
            pointer=ctypes.c_void_p.from_buffer(buffer).value
            return _sid_text(pointer,adv,kernel)
        finally:
            kernel.CloseHandle(token)
    finally:
        kernel.CloseHandle(process)


def request_once(suffix: str, payload: bytes, *,
                 expected_server_sid: str) -> bytes:
    """Client verifies the actual Windows server process token BEFORE sending.

    A named pipe name, unsigned response and caller-supplied process ID alone
    do not authenticate the server. The expected SID must come from a
    separately protected launcher configuration, never untrusted agent text.
    """
    _require_windows()
    full_name=_name(suffix)
    _validate_sid(expected_server_sid,"EXPECTED_SERVER_SID")
    if type(payload) is not bytes or not 1<=len(payload)<=MAX_FRAME:
        raise PipeAccessError("PIPE_CLIENT_PAYLOAD_INVALID")
    kernel=_kernel()
    deadline=time.monotonic()+3.0
    while not kernel.WaitNamedPipeW(full_name,200):
        if time.monotonic()>=deadline:
            raise PipeAccessError("PIPE_ENDPOINT_UNAVAILABLE")
        time.sleep(0.025)
    # SecurityImpersonation via SECURITY_SQOS_PRESENT|SECURITY_IMPERSONATION.
    handle=kernel.CreateFileW(full_name,0xC0000000,0,None,3,
                              FILE_FLAG_OVERLAPPED|0x00100000|0x00020000,None)
    if handle is None or handle==INVALID_HANDLE:
        raise PipeAccessError("PIPE_CLIENT_CONNECTION_DENIED")
    try:
        server_sid=server_process_sid(handle,kernel=kernel)
        if server_sid!=expected_server_sid:
            raise PipeAccessError("PIPE_SERVER_IDENTITY_DENIED")
        _write(handle,payload,kernel)
        return _read(handle,kernel,timeout_ms=2500)
    finally:
        kernel.CloseHandle(handle)
