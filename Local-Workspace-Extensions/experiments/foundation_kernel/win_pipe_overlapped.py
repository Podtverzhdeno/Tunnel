"""Bounded Win32 named-pipe I/O using overlapped operations.

Unlike synchronous ConnectNamedPipe/ReadFile/WriteFile, these calls have
hard finite waits and cancellation. Used only with handles opened using
FILE_FLAG_OVERLAPPED. No global threadpool, watchdog Python thread or
uninterruptible blocking operation is used.

If CancelIoEx fails to settle an operation, the hosting process must not
continue using a possibly live OVERLAPPED/buffer stack allocation.
The fail-closed path exits that *dedicated broker* process immediately.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as wt

PENDING = 997
MORE_DATA = 234
CONNECTED = 535
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
CANCELLED = 995
ERROR_NOT_FOUND = 1168
CLIENT_IO_TIMEOUT_MS = 2500
SERVER_READ_TIMEOUT_MS = 1600
SERVER_ACCEPT_TIMEOUT_MS = 3000
WRITE_TIMEOUT_MS = 1600
CANCEL_DRAIN_MS = 1500
FILE_FLAG_OVERLAPPED = 0x40000000

class TimedPipeError(ValueError):
    pass

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", wt.HANDLE),
    ]


def configure(kernel):
    """Attach explicit signatures; called after WinDLL loading on Windows."""
    if sys.platform != "win32":
        raise TimedPipeError("TIMED_PIPE_WINDOWS_ONLY")
    kernel.CreateEventW.argtypes=[
        ctypes.c_void_p,wt.BOOL,wt.BOOL,wt.LPCWSTR]
    kernel.CreateEventW.restype=wt.HANDLE
    kernel.WaitForSingleObject.argtypes=[wt.HANDLE,wt.DWORD]
    kernel.WaitForSingleObject.restype=wt.DWORD
    kernel.GetOverlappedResult.argtypes=[
        wt.HANDLE,ctypes.POINTER(OVERLAPPED),ctypes.POINTER(wt.DWORD),wt.BOOL]
    kernel.GetOverlappedResult.restype=wt.BOOL
    kernel.CancelIoEx.argtypes=[wt.HANDLE,ctypes.POINTER(OVERLAPPED)]
    kernel.CancelIoEx.restype=wt.BOOL
    return kernel


def _event(kernel):
    handle=kernel.CreateEventW(None,True,False,None)
    if not handle:
        raise TimedPipeError("PIPE_EVENT_CREATION_FAILED")
    return handle


def _drain_cancel(kernel, handle, operation, kind):
    kernel.CancelIoEx(handle,ctypes.byref(operation))
    if kind=="CONNECT":
        # Windows may retain a pending ConnectNamedPipe until explicitly
        # disconnecting the unconnected server end. Only use on timeout.
        kernel.DisconnectNamedPipe(handle)
    # Cancellation of Windows named pipes usually completes immediately.
    # We MUST not release the native buffer/OVERLAPPED while I/O is pending.
    result=kernel.WaitForSingleObject(operation.hEvent,CANCEL_DRAIN_MS)
    if result != WAIT_OBJECT_0:
        # A dedicated test/broker process must fail closed; otherwise freeing
        # the pending OVERLAPPED and stack buffer creates use-after-free.
        os._exit(124)
    transferred=wt.DWORD()
    # Nonblocking drain; cancelled I/O correctly returns FALSE/995.
    kernel.GetOverlappedResult(
        handle,ctypes.byref(operation),ctypes.byref(transferred),False)


def _finish(kernel, handle, operation, immediate, *, ms, kind):
    if type(ms) is not int or not 1<=ms<=30000:
        raise TimedPipeError("PIPE_DEADLINE_INVALID")
    transferred=wt.DWORD()
    if not immediate:
        error=ctypes.get_last_error()
        if error==MORE_DATA:
            raise TimedPipeError("PIPE_FRAME_TOO_LARGE")
        if error==CONNECTED and kind=="CONNECT":
            return 0
        if error!=PENDING:
            raise TimedPipeError("PIPE_"+kind+"_FAILED")
        waited=kernel.WaitForSingleObject(operation.hEvent,ms)
        if waited==WAIT_TIMEOUT:
            _drain_cancel(kernel,handle,operation,kind)
            raise TimedPipeError("PIPE_"+kind+"_TIMEOUT")
        if waited!=WAIT_OBJECT_0:
            _drain_cancel(kernel,handle,operation,kind)
            raise TimedPipeError("PIPE_"+kind+"_WAIT_FAILED")
    if not kernel.GetOverlappedResult(
        handle,ctypes.byref(operation),ctypes.byref(transferred),False):
        error=ctypes.get_last_error()
        if error==MORE_DATA:
            raise TimedPipeError("PIPE_FRAME_TOO_LARGE")
        raise TimedPipeError("PIPE_"+kind+"_RESULT_FAILED")
    return int(transferred.value)


def connect(kernel,handle,*,timeout_ms=SERVER_ACCEPT_TIMEOUT_MS):
    configure(kernel)
    event=_event(kernel)
    try:
        operation=OVERLAPPED()
        operation.hEvent=event
        outcome=bool(kernel.ConnectNamedPipe(handle,ctypes.byref(operation)))
        return _finish(kernel,handle,operation,outcome,ms=timeout_ms,
                       kind="CONNECT")
    finally:
        kernel.CloseHandle(event)


def read(kernel,handle,max_frame,*,timeout_ms=SERVER_READ_TIMEOUT_MS):
    configure(kernel)
    if type(max_frame) is not int or not 1<=max_frame<=65536:
        raise TimedPipeError("PIPE_FRAME_LIMIT_INVALID")
    event=_event(kernel)
    try:
        operation=OVERLAPPED()
        operation.hEvent=event
        buffer=ctypes.create_string_buffer(max_frame+1)
        transferred=wt.DWORD()
        outcome=bool(kernel.ReadFile(
            handle,buffer,max_frame+1,ctypes.byref(transferred),
            ctypes.byref(operation)))
        count=_finish(kernel,handle,operation,outcome,ms=timeout_ms,kind="READ")
        if count>max_frame:
            raise TimedPipeError("PIPE_FRAME_TOO_LARGE")
        return buffer.raw[:count]
    finally:
        kernel.CloseHandle(event)


def write(kernel,handle,raw,*,timeout_ms=WRITE_TIMEOUT_MS):
    configure(kernel)
    if type(raw) is not bytes or not 1<=len(raw)<=65536:
        raise TimedPipeError("PIPE_WRITE_INVALID")
    event=_event(kernel)
    try:
        operation=OVERLAPPED()
        operation.hEvent=event
        buffer=ctypes.create_string_buffer(raw)
        transferred=wt.DWORD()
        outcome=bool(kernel.WriteFile(
            handle,buffer,len(raw),ctypes.byref(transferred),
            ctypes.byref(operation)))
        count=_finish(kernel,handle,operation,outcome,ms=timeout_ms,kind="WRITE")
        if count!=len(raw):
            raise TimedPipeError("PIPE_WRITE_PARTIAL")
        return count
    finally:
        kernel.CloseHandle(event)
