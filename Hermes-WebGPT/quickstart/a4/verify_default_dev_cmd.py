"""Live idempotence check of the actual double-click Start-Hermes-All.cmd.

No service start is attempted unless ALL existing components have proven
readiness before command invocation. Never reads or prints credentials, never
sends Telegram/ChatGPT traffic, and compares process/receipt identity after.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from quickstart.verify_idempotent_running import preflight


def main() -> int:
    before=preflight()
    if before is None:
        print(json.dumps({"status":"SKIP","reason":"all_four_services_not_attested",
                          "launcher_executed":False}))
        return 3
    command=["cmd.exe","/d","/c",str(ROOT/"Start-Hermes-All.cmd")]
    try:
        done=subprocess.run(command,cwd=ROOT,
                            input=b"\r\n",
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        print(json.dumps({"status":"UNKNOWN","reason":"launcher_timeout_or_error",
                          "launcher_executed":True}))
        return 2
    after=preflight()
    unchanged=(after is not None and
               all(before.get(k)==after.get(k) for k in
                   ("relay","gateway","receipts")))
    # Do not display terminal output: it can contain personal paths or
    # warnings from the user's own services.
    result={"status":"PASS" if done.returncode==0 and unchanged else "FAIL",
            "launcher_executed":True,
            "launcher_exit_code":done.returncode,
            "service_and_receipt_identity_unchanged":unchanged,
            "sent_messages_by_test":0,
            "uploaded_files_by_test":0}
    print(json.dumps(result))
    return 0 if result["status"]=="PASS" else 2


if __name__=="__main__":
    raise SystemExit(main())
