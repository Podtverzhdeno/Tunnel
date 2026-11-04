"""P085 read-only Windows token/elevation preflight, no UAC or writes."""
from __future__ import annotations
import ctypes
import json
import os
def check():
    if os.name!="nt":
        return {"ok":False,"reason":"WINDOWS_REQUIRED"}
    try:
        shell=ctypes.WinDLL("shell32")
        shell.IsUserAnAdmin.restype=ctypes.c_int
        kernel=ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcessId.restype=ctypes.c_ulong
        kernel.ProcessIdToSessionId.argtypes=[ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)]
        kernel.ProcessIdToSessionId.restype=ctypes.c_int
        session=ctypes.c_ulong(0)
        ok=kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(),ctypes.byref(session))
        elevated=bool(shell.IsUserAnAdmin())
        return {"ok":True,"is_elevated_administrator":elevated,
                "windows_session_known":bool(ok),
                "windows_session_id":session.value if ok else None,
                "can_create_local_accounts_without_uac":elevated,
                "uac_approval_supplied":False,
                "system_changes_performed":False}
    except (OSError,AttributeError):
        return {"ok":False,"reason":"TOKEN_PROBE_UNAVAILABLE"}
if __name__=="__main__":
    print(json.dumps(check(),sort_keys=True))
