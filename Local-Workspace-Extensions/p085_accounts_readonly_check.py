"""Read-only P085 account inventory. Never passes -Apply or credentials.

Invokes the already reviewed PowerShell dry-run only; reports aggregates,
not account names, SIDs, passwords or private paths.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SOURCE=Path(__file__).resolve().parent/"Setup-P085WindowsPrincipals.ps1"


def check():
    if os.name != "nt":
        return {"ok":False,"error_code":"WINDOWS_REQUIRED"}
    try:
        run=subprocess.run(
            ["powershell.exe","-NoProfile","-NoLogo","-NonInteractive",
             "-File",str(SOURCE)],
            cwd=SOURCE.parent,stdin=subprocess.DEVNULL,
            capture_output=True,text=True,timeout=20)
    except (OSError,subprocess.TimeoutExpired):
        return {"ok":False,"error_code":"POWERSHELL_DRY_RUN_UNAVAILABLE"}
    if run.returncode!=0:
        return {"ok":False,"error_code":"POWERSHELL_DRY_RUN_FAILED"}
    try:
        obj=json.loads(run.stdout)
    except (ValueError,UnicodeError):
        return {"ok":False,"error_code":"INVALID_DRY_RUN_OUTPUT"}
    accounts=obj.get("accounts")
    if (type(obj) is not dict or obj.get("schema")!="p085-local-principals-plan-v1"
            or obj.get("dry_run") is not True
            or type(accounts) is not list or len(accounts)!=5):
        return {"ok":False,"error_code":"UNTRUSTED_DRY_RUN_SCHEMA"}
    states=[x.get("state") for x in accounts if type(x) is dict]
    if len(states)!=5 or any(x not in ("MISSING","EXISTS") for x in states):
        return {"ok":False,"error_code":"UNTRUSTED_ACCOUNT_STATUS"}
    return {
        "ok":True,
        "schema":"p085-local-principals-readonly-check-v1",
        "accounts_configured":states.count("EXISTS"),
        "accounts_missing":states.count("MISSING"),
        "account_owners_os_attested":False,
        "system_changes_performed":False,
        "service_started":False,
        "p085_production_enabled":False,
    }


if __name__=="__main__":
    print(json.dumps(check(),sort_keys=True))
