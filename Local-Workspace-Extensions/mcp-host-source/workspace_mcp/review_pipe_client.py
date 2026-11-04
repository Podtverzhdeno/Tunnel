"""P085 Windows reviewer pipe CLIENT; no signer and no review authority.

The caller must run as the separately provisioned reviewer Windows principal
and obtain a short-lived signed grant/QA receipt through trusted channels.
Both server SID AND pipe DACL must be validated; neither role strings nor
server identity from an agent message are trustworthy.

Uses local WinAPI named pipes. No IPC is started or secrets produced by import.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import struct
from ctypes import wintypes as w

from .host_pipe_peer import SID, TOKEN_QUERY, PeerIdentityError, _api, _sid_for_token
from .review_pipe_server import PIPE_NAME, MAX_FRAME

PIPE_DATA_RIGHTS = 0x0001 | 0x0002
OPEN_EXISTING = 3
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IMPERSONATION = 0x00020000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE = ctypes.c_void_p(-1).value


class PipeClientError(ValueError):
    """Stable errors only. Signed grants and QA receipts never appear in errors."""


def _kernel():
    if os.name != "nt":
        raise PipeClientError("WINDOWS_REQUIRED")
    lib=ctypes.WinDLL("kernel32",use_last_error=True)
    lib.WaitNamedPipeW.argtypes=[w.LPCWSTR,w.DWORD]
    lib.WaitNamedPipeW.restype=w.BOOL
    lib.CreateFileW.argtypes=[
        w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    lib.CreateFileW.restype=w.HANDLE
    lib.GetNamedPipeServerProcessId.argtypes=[w.HANDLE,ctypes.POINTER(w.ULONG)]
    lib.GetNamedPipeServerProcessId.restype=w.BOOL
    lib.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
    lib.OpenProcess.restype=w.HANDLE
    lib.ReadFile.argtypes=[
        w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    lib.ReadFile.restype=w.BOOL
    lib.WriteFile.argtypes=[
        w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    lib.WriteFile.restype=w.BOOL
    lib.CloseHandle.argtypes=[w.HANDLE]
    lib.CloseHandle.restype=w.BOOL
    return lib


def _server_sid(lib, handle):
    """Live OS process token identity; never trust a reply-provided SID."""
    pid=w.ULONG()
    if not lib.GetNamedPipeServerProcessId(handle,ctypes.byref(pid)):
        raise PipeClientError("SERVER_IDENTITY_UNAVAILABLE")
    if not pid.value:
        raise PipeClientError("SERVER_IDENTITY_UNAVAILABLE")
    proc=lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,False,pid)
    if not proc or proc==INVALID_HANDLE:
        raise PipeClientError("SERVER_IDENTITY_UNAVAILABLE")
    try:
        advapi,kernel,_=_api()
        token=w.HANDLE()
        if not advapi.OpenProcessToken(proc,TOKEN_QUERY,ctypes.byref(token)):
            raise PipeClientError("SERVER_TOKEN_UNAVAILABLE")
        try:
            return _sid_for_token(advapi,kernel,w,token)
        finally:
            kernel.CloseHandle(token)
    except PeerIdentityError as exc:
        raise PipeClientError("SERVER_IDENTITY_UNAVAILABLE") from exc
    finally:
        lib.CloseHandle(proc)


def _read_exact(lib,handle,count):
    data=bytearray()
    while len(data)<count:
        buf=ctypes.create_string_buffer(count-len(data))
        got=w.DWORD()
        if not lib.ReadFile(handle,buf,len(buf),ctypes.byref(got),None):
            raise PipeClientError("PIPE_READ_FAILED")
        if got.value<1 or got.value>len(buf):
            raise PipeClientError("PIPE_READ_INVALID")
        data.extend(buf.raw[:got.value])
    return bytes(data)


def _write_exact(lib,handle,data):
    # Bound all transport writes; zero-length and short writes fail closed.
    buf=ctypes.create_string_buffer(data)
    got=w.DWORD()
    if not lib.WriteFile(handle,buf,len(data),ctypes.byref(got),None):
        raise PipeClientError("PIPE_WRITE_FAILED")
    if got.value!=len(data):
        raise PipeClientError("PIPE_WRITE_SHORT")


def send_signed_review_request(
        task_id: str, signed_grant: dict, signed_qa_receipt: dict, *,
        expected_service_sid: str, timeout_ms: int=5000) -> dict:
    """Requires separately trusted expected_service_sid, NOT a chat value.

    A lost reply after write is UNKNOWN; do not retry a one-shot grant.
    """
    if (type(task_id) is not str or not re.fullmatch(r"P[0-9]{3,6}",task_id)
            or type(signed_grant) is not dict
            or type(signed_qa_receipt) is not dict):
        raise PipeClientError("INVALID_REVIEW_REQUEST")
    if (type(expected_service_sid) is not str
            or not SID.fullmatch(expected_service_sid)
            or type(timeout_ms) is not int
            or timeout_ms<100 or timeout_ms>30000):
        raise PipeClientError("INVALID_TRUSTED_CLIENT_CONFIG")
    try:
        raw=json.dumps(
            {"task_id":task_id, "signed_grant":signed_grant,
             "signed_qa_receipt":signed_qa_receipt},
            separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")
    except (TypeError,ValueError,UnicodeError) as exc:
        raise PipeClientError("INVALID_REVIEW_REQUEST") from exc
    if len(raw)<2 or len(raw)>MAX_FRAME-4:
        raise PipeClientError("REQUEST_TOO_LARGE")
    lib=_kernel()
    if not lib.WaitNamedPipeW(PIPE_NAME,timeout_ms):
        raise PipeClientError("PIPE_UNAVAILABLE")
    handle=lib.CreateFileW(
        PIPE_NAME,PIPE_DATA_RIGHTS,0,None,OPEN_EXISTING,
        SECURITY_SQOS_PRESENT|SECURITY_IMPERSONATION,None)
    if handle is None or handle==INVALID_HANDLE:
        raise PipeClientError("PIPE_ACCESS_DENIED")
    try:
        # This is checked BEFORE transmitting any signed payload. A malicious
        # pre-empting server pipe must not receive bearer credentials.
        if _server_sid(lib,handle)!=expected_service_sid:
            raise PipeClientError("UNTRUSTED_PIPE_SERVER")
        _write_exact(lib,handle,struct.pack("!I",len(raw))+raw)
        size=struct.unpack("!I",_read_exact(lib,handle,4))[0]
        if size<2 or size>MAX_FRAME:
            raise PipeClientError("INVALID_RESPONSE_LENGTH")
        try:
            response=json.loads(_read_exact(lib,handle,size))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipeClientError("INVALID_RESPONSE_JSON") from exc
        if (type(response) is not dict or type(response.get("ok")) is not bool
                or not set(response).issubset({"ok","result","error_code"})):
            raise PipeClientError("INVALID_RESPONSE_SCHEMA")
        _write_exact(lib,handle,b"\x06")
        return response
    finally:
        lib.CloseHandle(handle)
