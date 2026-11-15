"""Run only the Relay CLI help from the real, isolated, pinned A4 archive.

This is a Python import/entrypoint smoke, NOT a running service.
Never execute gateway_runner.py (it starts services on import) or start_bridge.py
(it starts the Relay on execution). No live ports or credentials are accessed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from quickstart.a4.runtime_stage import RuntimeStager

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT.parent / ".hermes-a4-runtime-v0.0.1-candidate.zip"
VERSION = "v0.0.1-candidate"
MANIFEST_SHA = "e61a3a2adef27a33736045a1c8ffbde8f9560ace9261d1d1a2291e8ba761ec8f"
ARCHIVE_SHA = "17347bcb9d14ff9aa30a24328eb2d629c6d355047d49d89bac5590be512975fa"


def smoke() -> dict:
    if not ARCHIVE.is_file() or ARCHIVE.is_symlink():
        return {"status": "BLOCKED", "reason": "candidate_missing"}
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA:
        return {"status": "BLOCKED", "reason": "candidate_hash_changed"}
    with tempfile.TemporaryDirectory(prefix="a4-staged-relay-help-") as td:
        stager = RuntimeStager(Path(td))
        result = stager.stage(ARCHIVE, VERSION, MANIFEST_SHA, ARCHIVE_SHA)
        if result["state"] != "STAGED_NOT_ACTIVATED":
            return {"status": "BLOCKED", "reason": "candidate_not_staged"}
        source = stager.versions / VERSION
        before = {p.name for p in source.iterdir()}
        # -E ignores user PYTHON* variables; -S disables site/packages;
        # -B disallows bytecode output. Do not execute any runtime daemon.
        env = {k: v for k, v in os.environ.items()
               if k.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR",
                                "SYSTEMDRIVE", "TEMP", "TMP", "COMSPEC"}}
        env["HERMES_MEDIA_ENABLED"] = "0"
        try:
            proc = subprocess.run(
                [sys.executable, "-E", "-S", "-B", "relay.py", "--help"],
                cwd=source, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=12,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               if os.name == "nt" else 0)
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "BLOCKED", "reason": "relay_help_failed"}
        output = proc.stdout.decode("utf8", "replace")
        after = {p.name for p in source.iterdir()}
        ok = (proc.returncode == 0 and
              "--max-active-requests" in output and
              "--data" in output and before == after)
        return {
            "status": "PASS" if ok else "BLOCKED",
            "staged_source_import_and_help": ok,
            "staged_files_unchanged": before == after,
            "relay_port_bound": False,
            "runtime_started": False,
            "gateway_started": False,
            "telegram_delivery_verified": False,
        }


if __name__ == "__main__":
    print(json.dumps(smoke()))
