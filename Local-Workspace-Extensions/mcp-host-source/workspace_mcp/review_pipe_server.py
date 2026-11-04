"""P085 Windows local-only reviewer named-pipe TRANSPORT (stdlib/ctypes).

Not registered in chat MCP tools. Dedicated privileged Windows service only.
Fail-closed if OS account separation is missing. Caller cannot claim identity,
actor role or SHA. The reviewer client's Windows SID is impersonated *after*
reading a bounded request, by review_grant_broker.ReviewGrantBroker.

Pipe SDDL allows SYSTEM, service principal full access; reviewer principal only
FILE_READ_DATA|FILE_WRITE_DATA + READ_CONTROL|SYNCHRONIZE (no right to create
another pipe instance). First-instance flag prevents pipe-name preemption.
Use externally provisioned Windows accounts and ACL; do NOT launch this from
the same principal as legacy unrestricted MCP.

No signing key, trusted configuration or protected account is created here.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import time
from ctypes import wintypes as w

from .review_grants import GrantDenied
from .review_grant_broker import BrokerPolicy, ReviewGrantBroker
from .host_pipe_peer import SID

PIPE_NAME = r"\\.\pipe\p085-trusted-review-v1"
PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_WAIT = 0x00000000
PIPE_NOWAIT = 0x00000001
PIPE_CONNECTED = 535
ERROR_NO_DATA = 232
READ_TIMEOUT_SECONDS = 8
ERROR_MORE_DATA = 234
MAX_FRAME = 8192
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

class PipeProtocolError(ValueError):
    pass


def pipe_sddl(policy: BrokerPolicy) -> str:
    policy.validate()
    # 0x00120003 = SYNCHRONIZE | READ_CONTROL | READ_DATA | WRITE_DATA,
    # explicitly NO FILE_CREATE_PIPE_INSTANCE bit (0x0004).
    return ("D:P(A;;GA;;;SY)"
            f"(A;;GA;;;{policy.service_sid})"
            f"(A;;0x00120003;;;{policy.reviewer_client_sid})")


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", w.BOOL)]


def _os_api():
    if os.name != "nt":
        raise PipeProtocolError("WINDOWS_REQUIRED")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(w.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    kernel.CreateNamedPipeW.argtypes = [
        w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
        ctypes.POINTER(_SecurityAttributes)]
    kernel.CreateNamedPipeW.restype = w.HANDLE
    kernel.ConnectNamedPipe.argtypes = [w.HANDLE, ctypes.c_void_p]
    kernel.ConnectNamedPipe.restype = w.BOOL
    kernel.SetNamedPipeHandleState.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD),
                                              ctypes.c_void_p, ctypes.c_void_p]
    kernel.SetNamedPipeHandleState.restype = w.BOOL
    kernel.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                                ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    kernel.ReadFile.restype = w.BOOL
    kernel.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                                 ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    kernel.WriteFile.restype = w.BOOL
    kernel.FlushFileBuffers.argtypes = [w.HANDLE]
    kernel.FlushFileBuffers.restype = w.BOOL
    kernel.DisconnectNamedPipe.argtypes = [w.HANDLE]
    kernel.DisconnectNamedPipe.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return kernel, advapi


def _create_pipe(policy, name=PIPE_NAME):
    if name != PIPE_NAME:
        raise PipeProtocolError("PIPE_NAME_NOT_ALLOWLISTED")
    kernel, advapi = _os_api()
    sec = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            pipe_sddl(policy), 1, ctypes.byref(sec), None):
        raise PipeProtocolError("PIPE_SDDL_INVALID")
    attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), sec, False)
    try:
        h = kernel.CreateNamedPipeW(
            PIPE_NAME, PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_REJECT_REMOTE_CLIENTS | PIPE_WAIT, 1, MAX_FRAME, MAX_FRAME,
            5000, ctypes.byref(attrs))
        if h is None or h == INVALID_HANDLE_VALUE:
            raise PipeProtocolError("PIPE_CREATE_DENIED")
        return kernel, h
    finally:
        kernel.LocalFree(sec)


def _read_exact(kernel, pipe, length):
    data = bytearray()
    deadline=time.monotonic()+READ_TIMEOUT_SECONDS
    while len(data) < length:
        if time.monotonic() >= deadline:
            raise PipeProtocolError("PIPE_READ_TIMEOUT")
        remaining = length - len(data)
        buf = ctypes.create_string_buffer(remaining)
        got = w.DWORD()
        if not kernel.ReadFile(pipe, buf, remaining, ctypes.byref(got), None):
            if ctypes.get_last_error()==ERROR_NO_DATA:
                time.sleep(0.01)
                continue
            raise PipeProtocolError("PIPE_READ_FAILED")
        if got.value < 1 or got.value > remaining:
            raise PipeProtocolError("PIPE_READ_EMPTY")
        data.extend(buf.raw[:got.value])
    return bytes(data)


def _read_message(kernel, pipe):
    import struct
    header = _read_exact(kernel, pipe, 4)
    size = struct.unpack("!I", header)[0]
    if size < 2 or size > MAX_FRAME:
        raise PipeProtocolError("INVALID_FRAME_LENGTH")
    try:
        data = json.loads(_read_exact(kernel, pipe, size))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipeProtocolError("INVALID_JSON_FRAME") from exc
    if type(data) is not dict:
        raise PipeProtocolError("INVALID_BROKER_REQUEST")
    return data


def _write_message(kernel, pipe, document):
    import struct
    raw=json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(raw) > MAX_FRAME - 4:
        raise PipeProtocolError("RESPONSE_TOO_LARGE")
    packet=struct.pack("!I",len(raw))+raw
    buf=ctypes.create_string_buffer(packet)
    written=w.DWORD()
    if not kernel.WriteFile(pipe,buf,len(packet),ctypes.byref(written),None):
        raise PipeProtocolError("PIPE_WRITE_FAILED")
    if written.value != len(packet):
        raise PipeProtocolError("SHORT_PIPE_WRITE")


def serve_one(policy: BrokerPolicy, broker: ReviewGrantBroker, *,
              qa_policy=None, qa_verification_key=None,
              trusted_finalizer=None) -> dict:
    """Handle one local request, close pipe regardless of error.

    Acceptance-scoped reservations MUST include a valid independent QA
    attestation. This transport cannot authorize an acceptance transition.
    """
    from .review_qa_attestation import QATrustPolicy
    if not isinstance(broker, ReviewGrantBroker):
        raise PipeProtocolError("TRUSTED_BROKER_REQUIRED")
    if (type(qa_policy) is not QATrustPolicy or
            type(qa_verification_key) is not bytes or
            len(qa_verification_key) < 32):
        raise PipeProtocolError("INDEPENDENT_QA_GATE_REQUIRED")
    if trusted_finalizer is not None:
        from .review_finalizer import ProtectedReviewFinalizer
        if type(trusted_finalizer) is not ProtectedReviewFinalizer:
            raise PipeProtocolError("TRUSTED_FINALIZER_INVALID")
    kernel, handle = _create_pipe(policy)
    connected=False
    try:
        if not kernel.ConnectNamedPipe(handle, None):
            if ctypes.get_last_error() != PIPE_CONNECTED:
                raise PipeProtocolError("PIPE_CONNECT_FAILED")
        connected=True
        # Never allow a client that sends only a frame prefix to hold the
        # privileged service thread indefinitely. Nonblocking ReadFile plus
        # a monotonic deadline bounds each request independently of OS I/O.
        mode=w.DWORD(PIPE_NOWAIT)
        if not kernel.SetNamedPipeHandleState(handle,ctypes.byref(mode),None,None):
            raise PipeProtocolError("PIPE_NONBLOCKING_REQUIRED")
        try:
            request=_read_message(kernel,handle)
            result=broker.reserve_with_independent_qa(
                request, connected_pipe_handle=int(handle),
                qa_policy=qa_policy, qa_key=qa_verification_key)
            if trusted_finalizer is not None:
                # Only an independently provisioned, owner-pinned finalizer
                # in the protected Windows service may perform file promotion.
                result=trusted_finalizer.accept_reserved(request,result)
            response={"ok":True,"result":result}
        except (GrantDenied, PipeProtocolError) as exc:
            # Stable codes only. Never return signing key, lease, SID, raw
            # rejection detail or traceback to an untrusted client.
            code=str(exc)
            if len(code)>80 or not re.fullmatch(r"[A-Z0-9_]+",code):
                code="REVIEW_REQUEST_DENIED"
            response={"ok":False,"error_code":code}
        except Exception:
            # A crash after preparing/applying some files can leave a durable
            # MergeIntent. Never leak stacktraces, replay the grant or claim
            # acceptance; owner inspects journal and DB to reconcile.
            response={"ok":False,"error_code":"OWNER_RECONCILIATION_REQUIRED"}
        try:
            _write_message(kernel,handle,response)
            # FlushFileBuffers(named pipe) may block FOREVER if the client
            # never reads. Instead require bounded read-after-response ACK.
            # The client must read the full frame and send b"\\x06".
            try:
                if _read_exact(kernel,handle,1)!=b"\x06":
                    response={**response,"delivery_acknowledged":False}
                else:
                    response={**response,"delivery_acknowledged":True}
            except PipeProtocolError:
                response={**response,"delivery_acknowledged":False}
        except PipeProtocolError:
            response={**response,"delivery_acknowledged":False}
        return response
    finally:
        if connected:
            kernel.DisconnectNamedPipe(handle)
        kernel.CloseHandle(handle)
