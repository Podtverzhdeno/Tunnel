"""Read-only, authenticated local Relay capacity probe for Hermes DEV.

Unlike a configured workers count, this asks the *running* relay about its
actual effective max_active_requests. No requests are admitted or claimed.
Never prints the bearer key, private routing IDs, or job payloads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
AUTH_FILE = ROOT / "data" / "relay-auth.json"
URL = "http://127.0.0.1:18765/bridge/status"


def _live_fetch(key: str) -> Any:
    req = urllib.request.Request(
        URL, headers={"Authorization": f"Bearer {key}"}, method="GET"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=2.5) as response:
        if response.status != 200:
            raise ValueError("relay_status_not_200")
        return json.loads(response.read(8192).decode("utf-8"))


def capacity_report(*, auth_file: Path = AUTH_FILE,
                    fetch: Callable[[str], Any] = _live_fetch) -> dict:
    result = {
        "local_relay_authenticated": False,
        "configured_slots_observed": None,
        "running_relay_slots": None,
        "at_least_three_slots_active": False,
        "three_parallel_chat_requests_e2e_verified": False,
        "bridge_send_verified": False,
        "status": "UNVERIFIED",
    }
    try:
        config = ROOT / "relay_capacity.json"
        if config.is_file() and not config.is_symlink() and config.stat().st_size <= 1024:
            obj = json.loads(config.read_text(encoding="utf-8"))
            slots = obj.get("max_active_requests") if isinstance(obj, dict) else None
            if type(slots) is int and 1 <= slots <= 25:
                result["configured_slots_observed"] = slots
        if auth_file.is_symlink() or not auth_file.is_file():
            result["status"] = "AUTH_FILE_UNAVAILABLE"
            return result
        if auth_file.stat().st_size > 2048:
            result["status"] = "AUTH_FILE_INVALID"
            return result
        stored = json.loads(auth_file.read_text(encoding="utf-8"))
        token = stored.get("key") if isinstance(stored, dict) else None
        if not isinstance(token, str) or len(token) < 24 or len(token) > 512:
            result["status"] = "AUTH_FILE_INVALID"
            return result
        data = fetch(token)
        if not isinstance(data, dict) or data.get("ok") is not True:
            result["status"] = "RELAY_UNEXPECTED_RESPONSE"
            return result
        actual = data.get("max_active_requests")
        if type(actual) is not int or not 1 <= actual <= 25:
            result["status"] = "RELAY_UNEXPECTED_RESPONSE"
            return result
        result["local_relay_authenticated"] = True
        result["running_relay_slots"] = actual
        result["at_least_three_slots_active"] = actual >= 3
        result["status"] = "RUNNING_CAPACITY_CONFIRMED"
        return result
    except (OSError, ValueError, UnicodeError, TypeError, KeyError,
            urllib.error.URLError, TimeoutError):
        result["status"] = "RELAY_STATUS_UNAVAILABLE"
        return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args(argv)
    report = capacity_report()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["local_relay_authenticated"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
