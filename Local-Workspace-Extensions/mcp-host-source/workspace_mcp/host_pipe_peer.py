"""P085 Windows named-pipe peer SID: OS token impersonation, NOT client JSON.

Only a dedicated OS-isolated Windows broker process may call this on a valid,
connected named-pipe HANDLE *after reading a request from that client*.
No HTTP headers, bearer names, self-declared role or PID-based identity trust.

The caller must set a strict named-pipe DACL and run under a Windows principal
different from all untrusted scheduled agents. This module grants NO access.
"""
from __future__ import annotations

import ctypes
import os
import re

SID = re.compile(r"S-1-(?:[0-9]+-){1,16}[0-9]+\Z")
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
MAX_TOKEN_BYTES = 65536

class PeerIdentityError(ValueError):
    pass


def _api():
    if os.name != "nt":
        raise PeerIdentityError("WINDOWS_REQUIRED")
    from ctypes import wintypes as w
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.OpenThreadToken.argtypes = [w.HANDLE, w.DWORD, w.BOOL, ctypes.POINTER(w.HANDLE)]
    advapi.OpenThreadToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_uint,
                                           ctypes.c_void_p, w.DWORD,
                                           ctypes.POINTER(w.DWORD)]
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(w.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = w.BOOL
    advapi.ImpersonateNamedPipeClient.argtypes = [w.HANDLE]
    advapi.ImpersonateNamedPipeClient.restype = w.BOOL
    advapi.RevertToSelf.argtypes = []
    advapi.RevertToSelf.restype = w.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetCurrentThread.argtypes = []
    kernel.GetCurrentThread.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel, w


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", ctypes.c_uint32)]


def _sid_for_token(advapi, kernel, w, token):
    needed = w.DWORD(0)
    advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
    if needed.value < ctypes.sizeof(_SidAndAttributes) or needed.value > MAX_TOKEN_BYTES:
        raise PeerIdentityError("TOKEN_USER_INVALID")
    buf = ctypes.create_string_buffer(needed.value)
    if not advapi.GetTokenInformation(token, TOKEN_USER, buf, needed,
                                     ctypes.byref(needed)):
        raise PeerIdentityError("TOKEN_READ_FAILED")
    pointer = ctypes.cast(buf, ctypes.POINTER(_SidAndAttributes)).contents.sid
    if not pointer:
        raise PeerIdentityError("TOKEN_SID_MISSING")
    out = w.LPWSTR()
    if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(out)):
        raise PeerIdentityError("SID_CONVERSION_FAILED")
    try:
        sid = out.value
    finally:
        kernel.LocalFree(ctypes.cast(out, ctypes.c_void_p))
    if not sid or not SID.fullmatch(sid):
        raise PeerIdentityError("INVALID_WINDOWS_SID")
    return sid


def current_process_sid():
    advapi, kernel, w = _api()
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY,
                                  ctypes.byref(token)):
        raise PeerIdentityError("CURRENT_TOKEN_UNAVAILABLE")
    try:
        return _sid_for_token(advapi, kernel, w, token)
    finally:
        kernel.CloseHandle(token)


def named_pipe_client_sid(connected_pipe_handle):
    """Requires request bytes already read from THIS client's pipe.

    Always revert Windows thread impersonation, including errors; do not
    call third-party Python code between impersonation and RevertToSelf.
    """
    if (type(connected_pipe_handle) is not int or
            connected_pipe_handle <= 0 or
            connected_pipe_handle == ctypes.c_void_p(-1).value):
        raise PeerIdentityError("INVALID_PIPE_HANDLE")
    advapi, kernel, w = _api()
    if not advapi.ImpersonateNamedPipeClient(w.HANDLE(connected_pipe_handle)):
        raise PeerIdentityError("PIPE_CLIENT_IMPERSONATION_FAILED")
    token = w.HANDLE()
    try:
        if not advapi.OpenThreadToken(kernel.GetCurrentThread(), TOKEN_QUERY,
                                     True, ctypes.byref(token)):
            raise PeerIdentityError("PIPE_CLIENT_TOKEN_UNAVAILABLE")
        try:
            return _sid_for_token(advapi, kernel, w, token)
        finally:
            kernel.CloseHandle(token)
    finally:
        if not advapi.RevertToSelf():
            # A failed revert leaves the thread impersonating a client. Do
            # not reuse the thread for any privileged code in this case.
            raise PeerIdentityError("THREAD_IMPERSONATION_REVERT_FAILED")
