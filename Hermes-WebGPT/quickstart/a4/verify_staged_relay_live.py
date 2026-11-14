"""Real, isolated Hermes A4 Runtime ZIP Relay HTTP smoke on Windows.

Starts a test Relay from the pinned offline source ZIP on ephemeral port 0
with fresh temporary state. Never touches the live :18765 Relay, Gateway,
MCP, Tunnel, browser, Telegram, or any configured secrets. No POSTs or jobs.
The test-owned process is always terminated and awaited.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import tempfile
import threading
import urllib.request

from quickstart.a4.runtime_stage import RuntimeStager
from quickstart.a4.verify_staged_relay_help import (
    ARCHIVE, VERSION, MANIFEST_SHA, ARCHIVE_SHA
)


def isolated_http_smoke() -> dict:
    if not ARCHIVE.is_file() or ARCHIVE.is_symlink():
        return {"status": "BLOCKED", "reason": "local_archive_missing"}
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA:
        return {"status": "BLOCKED", "reason": "archive_digest_mismatch"}
    # A completely separate process / SQLite database / token / socket.
    with tempfile.TemporaryDirectory(prefix="hermes-a4-relay-live-isolated-") as tmp:
        folder = Path(tmp)
        staging = RuntimeStager(folder)
        staging.stage(ARCHIVE, VERSION, MANIFEST_SHA, ARCHIVE_SHA)
        source = staging.versions / VERSION
        state = folder / "runtime-data"
        state.mkdir()
        env = {k: v for k, v in os.environ.items() if k.upper() in (
            "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT",
            "COMSPEC", "TEMP", "TMP"
        )}
        env["HERMES_MEDIA_ENABLED"] = "0"
        cmd = [
            sys.executable, "-E", "-S", "-B", "relay.py",
            "--data", str(state),
            "--port", "0",
            "--max-active-requests", "3",
        ]
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=source, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               if os.name == "nt" else 0),
            )
            assert proc.stdout is not None
            ready = Queue(maxsize=1)
            threading.Thread(target=lambda: ready.put(proc.stdout.readline()),
                             daemon=True).start()
            try:
                first = ready.get(timeout=12)
            except Empty:
                return {"status": "BLOCKED", "reason": "isolated_start_timeout"}
            try:
                opened = json.loads(first)
            except (ValueError, UnicodeError, TypeError):
                return {"status": "BLOCKED", "reason": "invalid_startup_receipt"}
            port = opened.get("port")
            if (opened.get("ok") is not True
                    or type(port) is not int or not 1024 <= port <= 65535
                    or opened.get("max_active_requests") != 3):
                return {"status": "BLOCKED", "reason": "startup_shape_invalid"}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                health = json.load(r)
            if (health.get("ok") is not True
                    or health.get("service") != "hermes-webgpt-relay"
                    or health.get("protocol") != 1):
                return {"status": "BLOCKED", "reason": "health_invalid"}
            auth = json.loads((state / "relay-auth.json").read_text("utf8"))
            token = auth.get("key")
            if not isinstance(token, str) or len(token) < 30:
                return {"status": "BLOCKED", "reason": "isolated_auth_invalid"}
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/bridge/status",
                headers={"Authorization": "Bearer " + token},
                method="GET",
            )
            with opener.open(req, timeout=3) as r:
                status = json.load(r)
            ok = (status.get("ok") is True
                  and status.get("max_active_requests") == 3
                  and status.get("active_requests") == 0
                  and status.get("active_http_waiters") == 0)
            return {
                "status": "PASS" if ok else "BLOCKED",
                "isolated_relay_http_served": ok,
                "capacity_slots": status.get("max_active_requests") if ok else None,
                "no_jobs_issued": True,
                "temporary_state_only": True,
                "production_port_touched": False,
                "telegram_or_chatgpt_delivery_verified": False,
            }
        except (OSError, ValueError, RuntimeError,
                urllib.error.URLError, subprocess.SubprocessError):
            return {"status": "BLOCKED", "reason": "isolated_runtime_error"}
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout is not None:
                    proc.stdout.close()


if __name__ == "__main__":
    result = isolated_http_smoke()
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "PASS" else 2)
