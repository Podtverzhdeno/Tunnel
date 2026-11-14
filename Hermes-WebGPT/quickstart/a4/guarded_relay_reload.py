"""One-shot, guarded reload of an idle locally owned Hermes Relay.

Strong gates: pinned tested ZIP matches local relay.py & launcher, loaded
runtime is stale, existing A1 Relay guard attests exact parent/child ownership,
idle queue and idle gateway twice. Never replays tasks or sends Telegram.
If the operation becomes uncertain, DO NOT RETRY: reconcile existing PID,
receipt, listener and SQLite state manually before further action.

Requires explicit --apply. Without it, read-only preflight.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
from pathlib import Path
import sys
import zipfile

from quickstart.a4.loaded_runtime_parity import compare_loaded, ROOT
from quickstart.a4.relay_capacity_observer import capacity_report
from quickstart import services
from quickstart.a4.relay_reload_preflight import plan, GUARD

PINNED_ZIP = ROOT.parent / ".hermes-a4-runtime-v0.0.1-candidate.zip"
EXPECTED_ZIP_SHA = "17347bcb9d14ff9aa30a24328eb2d629c6d355047d49d89bac5590be512975fa"
ALLOWED = frozenset(("relay.py", "start_bridge.py"))


def _pinned_sources_attested() -> bool:
    if PINNED_ZIP.is_symlink() or not PINNED_ZIP.is_file():
        return False
    raw = PINNED_ZIP.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_ZIP_SHA:
        return False
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        spec = json.loads(archive.read("manifest.json"))
        items = {x["path"]: x for x in spec["files"]}
        for name in ALLOWED:
            local = ROOT / name
            if local.is_symlink() or not local.is_file():
                return False
            if (hashlib.sha256(local.read_bytes()).hexdigest()
                    != items[name]["sha256"]):
                return False
    return True


def guarded_once(*, apply: bool = False) -> dict:
    checked = plan()
    report = {
        "preflight": checked["reconciliation"],
        "candidate_sources_pinned": False,
        "apply_requested": apply,
        "reload_attempted": False,
        "reload_outcome": "NOT_ATTEMPTED",
        "loaded_source_match": False,
        "active_relay_slots": None,
        "telegram_or_chatgpt_messages_sent_by_operation": 0,
        "request_replays_by_operation": 0,
    }
    if checked["reconciliation"] != "READY_FOR_EXPLICIT_RELOAD":
        return report
    try:
        if not _pinned_sources_attested():
            report["preflight"] = "CANDIDATE_SOURCE_DRIFT"
            return report
    except (ValueError, OSError, KeyError, zipfile.BadZipFile):
        report["preflight"] = "CANDIDATE_NOT_VERIFIED"
        return report
    report["candidate_sources_pinned"] = True
    if not apply:
        report["reload_outcome"] = "READY_ONLY"
        return report

    try:
        spec = importlib.util.spec_from_file_location("a4_owned_relay_guard", GUARD)
        if spec is None or spec.loader is None:
            return report
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        guard.TARGET_SHA = hashlib.sha256((ROOT / "relay.py").read_bytes()).hexdigest()
        # Ensure this process has an unambiguous gateway identity before
        # stopping the Relay. Guard independently checks it is idle.
        gateway_before = services.gateway_plan()
        if gateway_before.get("action") != "already_running":
            report["preflight"] = "GATEWAY_IDENTITY_UNVERIFIED"
            return report
        if guard.preflight().get("ok") is not True:
            report["preflight"] = "GUARD_PRECHECK_FAILED"
            return report
        report["reload_attempted"] = True
        # The owner guard performs a second CAS-like preflight right before
        # stopping specific owned PIDs. Do not expose their IDs in receipts.
        command = sys.argv
        try:
            sys.argv = [str(GUARD), "--apply"]
            with contextlib.redirect_stdout(io.StringIO()):
                guard.main()
        finally:
            sys.argv = command
        after = compare_loaded()
        slots = capacity_report()
        gateway_after = services.gateway_plan()
        matching = (after["status"] == "MATCH"
                    and slots.get("local_relay_authenticated") is True
                    and slots.get("running_relay_slots") == 3
                    and gateway_after == gateway_before)
        report["loaded_source_match"] = after["status"] == "MATCH"
        report["active_relay_slots"] = slots.get("running_relay_slots")
        report["reload_outcome"] = ("VERIFIED" if matching
                                    else "UNKNOWN_RECONCILE_MANUALLY")
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError,
            TypeError, AttributeError, zipfile.BadZipFile,
            subprocess.SubprocessError, sqlite3.Error):
        # Could have failed before or after process termination. No retry.
        if report["reload_attempted"]:
            report["reload_outcome"] = "UNKNOWN_RECONCILE_MANUALLY"
        else:
            report["preflight"] = "GUARD_BLOCKED_BEFORE_STOP"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    result = guarded_once(apply=options.apply)
    print(json.dumps(result, ensure_ascii=False))
    no_reload_needed = (
        result["preflight"] == "NOT_REQUIRED"
        and result["reload_outcome"] == "NOT_ATTEMPTED"
    )
    raise SystemExit(0 if result["reload_outcome"] in ("READY_ONLY", "VERIFIED")
                     or no_reload_needed else 2)
