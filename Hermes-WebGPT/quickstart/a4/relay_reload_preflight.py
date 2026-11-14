"""Read-only preflight for reloading the installed Relay after source drift.

Reuses the A1/Relay-owned restart guard's full process-owner, idle queue,
source SHA and gateway-idle checks. No stop/start/retry occurs. Refuses
unknown states; does not print PIDs, request IDs or credentials.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from quickstart.a4.loaded_runtime_parity import compare_loaded, ROOT

GUARD = ROOT / "restart_relay_parent_child_guarded_20261110.py"


def plan() -> dict:
    current = compare_loaded()
    result = {
        "loaded_runtime_status": current["status"],
        "reconciliation": "NOT_REQUIRED" if current["status"] == "MATCH" else "BLOCKED",
        "source_changed_since_load": current["changed_since_loaded"],
        "full_owner_and_idle_attestation": False,
        "would_stop_processes": False,
        "would_restart_relay": False,
        "no_chatgpt_or_telegram_messages_sent": True,
    }
    if current["status"] != "DISK_CHANGED_AFTER_RELAY_START":
        return result
    if GUARD.is_symlink() or not GUARD.is_file():
        result["reconciliation"] = "GUARD_SOURCE_MISSING"
        return result
    try:
        spec = importlib.util.spec_from_file_location("a4_readonly_relay_guard", GUARD)
        if spec is None or spec.loader is None:
            result["reconciliation"] = "GUARD_LOAD_FAILED"
            return result
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.TARGET_SHA = hashlib.sha256((ROOT / "relay.py").read_bytes()).hexdigest()
        audit = module.preflight()
        if not isinstance(audit, dict) or audit.get("ok") is not True:
            result["reconciliation"] = "PREFLIGHT_FAILED"
            return result
        result["reconciliation"] = "READY_FOR_EXPLICIT_RELOAD"
        result["full_owner_and_idle_attestation"] = True
    except (OSError, ValueError, RuntimeError, AssertionError, AttributeError,
            KeyError, TypeError):
        result["reconciliation"] = "PREFLIGHT_BLOCKED"
    return result


if __name__ == "__main__":
    print(json.dumps(plan(), ensure_ascii=False))
