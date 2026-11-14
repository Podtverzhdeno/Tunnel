"""Read-only comparison of the *loaded* Relay build against local source.

The Relay captures runtime_build SHA256 once on import. A valid /health
response therefore distinguishes the process loaded earlier from source
changed on disk during development. The diagnostic NEVER restarts a process,
reads secrets, or sends a model request. Loopback-only, proxy-disabled GET.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = (
    "relay.py",
    "hermes_tool_schema_guard.py",
    "model_prompt_compaction.py",
    "model_news_prefetch.py",
)
HEALTH = "http://127.0.0.1:18765/health"
HASH = re.compile(r"^[0-9a-f]{64}$")


def _live_health() -> Any:
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(HEALTH, method="GET"), timeout=2.5) as resp:
        if resp.status != 200:
            raise ValueError("health_not_200")
        return json.loads(resp.read(8192))


def compare_loaded(*, root: Path = ROOT,
                   fetch: Callable[[], Any] = _live_health,
                   observed: Any = None) -> dict:
    out = {
        "status": "UNKNOWN",
        "loaded_build_hashes_valid": False,
        "source_hashes_checked": False,
        "loaded_equals_current_source": False,
        "changed_since_loaded": [],
        "files_checked": 0,
        "chatgpt_e2e_verified": False,
        "process_restarted": False,
    }
    try:
        health = observed if observed is not None else fetch()
        if (not isinstance(health, dict) or
            health.get("ok") is not True or
            health.get("service") != "hermes-webgpt-relay" or
            health.get("protocol") != 1):
            out["status"] = "RELAY_NOT_ATTESTED"
            return out
        hashes = health.get("runtime_build")
        if (not isinstance(hashes, dict) or
            any(not isinstance(hashes.get(n), str) or
                HASH.fullmatch(hashes.get(n)) is None for n in RUNTIME_FILES)):
            out["status"] = "LOADED_BUILD_UNAVAILABLE"
            return out
        out["loaded_build_hashes_valid"] = True
        if root.is_symlink() or not root.is_dir():
            out["status"] = "SOURCE_ROOT_UNAVAILABLE"
            return out
        actual = {}
        for name in RUNTIME_FILES:
            file = root / name
            if (file.is_symlink() or not file.is_file() or
                file.stat().st_size > 2_000_000 or
                not file.resolve(strict=True).is_relative_to(root.resolve(strict=True))):
                out["status"] = "SOURCE_FILE_UNVERIFIED"
                return out
            actual[name] = hashlib.sha256(file.read_bytes()).hexdigest()
        out["source_hashes_checked"] = True
        out["files_checked"] = len(RUNTIME_FILES)
        changed = [name for name in RUNTIME_FILES if hashes[name] != actual[name]]
        out["changed_since_loaded"] = changed
        out["loaded_equals_current_source"] = not changed
        out["status"] = ("MATCH" if not changed else "DISK_CHANGED_AFTER_RELAY_START")
    except (OSError, ValueError, TypeError, UnicodeError, URLError):
        out["status"] = "HEALTH_OR_SOURCE_ERROR"
    return out


if __name__ == "__main__":
    print(json.dumps(compare_loaded(), ensure_ascii=False))
