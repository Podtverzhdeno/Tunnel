"""Read-only check for ChatGPT Desktop and deep-link registration. No credentials or account data."""
import json
import os
import platform
import shutil
import subprocess
import sys
def schemes():
    out={}
    try:
        import winreg
        for name in ("codex", "chatgpt"):
            item={"registered":False}
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, name) as k:
                    item["registered"]=True
                    try:
                        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, name+r"\shell\open\command") as ck:
                            cmd=winreg.QueryValueEx(ck, "")[0]
                            item["handler_type"]="app_protocol" if ("AppX" in cmd or "ApplicationFrameHost" in cmd) else "desktop_protocol"
                    except OSError:
                        item["handler_type"]="unknown"
            except OSError: pass
            out[name]=item
    except ImportError:
        pass
    return out
def desktop_process():
    # Do not print other processes' command lines, IDs, or user names.
    if os.name!="nt": return None
    try:
        result=subprocess.run(["tasklist","/FO","CSV","/NH"],capture_output=True,text=True,timeout=8)
        names=[line.split('","')[0].lstrip('"').lower() for line in result.stdout.splitlines()]
        return any(("chatgpt" in n or n in ("codex.exe",)) for n in names)
    except (OSError,subprocess.TimeoutExpired): return None
def main():
    print(json.dumps({"platform":platform.system(),"python_ok":True,
    "python_executable_name":os.path.basename(sys.executable),
    "python_on_path":bool(shutil.which("python")),
    "chatgpt_desktop_process_seen":desktop_process(),
    "uri_schemes":schemes(),
    "note":"Absence of URI registration does not prove the app is not installed."},ensure_ascii=False))
if __name__=="__main__": main()
