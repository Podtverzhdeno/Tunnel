"""A4 DEV OneClick idempotency E2E on an already-running Windows host.

The launcher is only invoked if MCP, Tunnel, Relay and Gateway are already
attested as running. No bot messages, browser operations, file uploads, or
deliberate restarts are performed. Any unknown status aborts before launch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
# Local Workspace executes this file by path; make the project root
# importable in that mode as well as via "python -m".
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from quickstart.verify_idempotent_running import preflight


def verify_dev_start(*, inspect: Callable[[], dict | None] = preflight,
                     execute: Callable[..., Any] = subprocess.run) -> dict:
    before = inspect()
    if before is None:
        return {"status": "SKIP", "reason": "four_services_not_attested",
                "launch_attempted": False}
    if not isinstance(before, dict) or any(k not in before
                                            for k in ("relay", "gateway", "receipts")):
        return {"status": "SKIP", "reason": "unknown_initial_snapshot",
                "launch_attempted": False}
    # Don't run the ordinary launcher: this must explicitly exercise DEV.
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "quickstart" / "start.ps1"), "-DevMode",
    ]
    try:
        result = execute(cmd, cwd=ROOT, capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "UNKNOWN", "reason": "launcher_outcome_unknown",
                "launch_attempted": True}
    after = inspect()
    if after is None:
        return {"status": "FAIL", "reason": "service_attestation_lost",
                "launch_attempted": True,
                "launcher_exitcode": result.returncode}
    stable = all(before.get(k) == after.get(k)
                 for k in ("relay", "gateway", "receipts"))
    # No direct messages/files are transmitted by the test.
    return {
        "status": ("PASS" if stable and result.returncode == 0 else "FAIL"),
        "launcher_exitcode": result.returncode,
        "launch_attempted": True,
        "all_four_services_attested": True,
        "service_identities_unchanged": stable,
        "messages_sent_by_test": 0,
        "files_uploaded_by_test": 0,
        "browser_actions_by_test": 0,
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--preflight-only",action="store_true")
    args=parser.parse_args(argv)
    if args.preflight_only:
        state=preflight()
        payload={"status":"READY_TO_TEST" if state is not None else "SKIP",
                 "launch_attempted":False,"all_four_services_attested":state is not None}
    else:
        payload=verify_dev_start()
    print(json.dumps(payload,ensure_ascii=False))
    return {"PASS":0,"READY_TO_TEST":0,"SKIP":3}.get(payload["status"],2)


if __name__=="__main__":
    raise SystemExit(main())
