"""Three concurrent SYNTHETIC Relay HTTP roundtrips from offline A4 ZIP.

All activity is confined to a new ephemeral localhost Relay with temporary
SQLite and auth. No live Hermes jobs, Telegram messages, browser tabs or
actual ChatGPT Send. The three fake browser receipts are labelled synthetic.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from quickstart.a4.runtime_stage import RuntimeStager
from quickstart.a4.verify_staged_relay_help import (
    ARCHIVE, VERSION, MANIFEST_SHA, ARCHIVE_SHA
)


def _request(port: int, token: str, path: str, payload: dict | None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=11)
    try:
        raw = json.dumps(payload).encode("utf8") if payload is not None else None
        conn.request("POST" if payload is not None else "GET", path, raw, {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        })
        result = conn.getresponse()
        body = result.read(131072)
        return result.status, json.loads(body)
    finally:
        conn.close()


def isolated_three_requests() -> dict:
    if not ARCHIVE.is_file() or ARCHIVE.is_symlink():
        return {"status": "BLOCKED", "reason": "archive_missing"}
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA:
        return {"status": "BLOCKED", "reason": "archive_digest_mismatch"}
    with tempfile.TemporaryDirectory(prefix="a4-relay-three-synthetic-") as folder:
        root = Path(folder)
        staged = RuntimeStager(root)
        staged.stage(ARCHIVE, VERSION, MANIFEST_SHA, ARCHIVE_SHA)
        source = staged.versions / VERSION
        state = root / "private-runtime-data"
        state.mkdir()
        env = {k: v for k, v in os.environ.items() if k.upper() in (
            "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
            "TEMP", "TMP", "COMSPEC",
        )}
        env["HERMES_MEDIA_ENABLED"] = "0"
        command = [
            sys.executable, "-E", "-S", "-B", "relay.py",
            "--data", str(state), "--port", "0", "--max-active-requests", "3"
        ]
        proc = None
        try:
            proc = subprocess.Popen(command, cwd=source, env=env,
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                    if os.name == "nt" else 0)
            ready = Queue(maxsize=1)
            threading.Thread(target=lambda: ready.put(proc.stdout.readline()),
                             daemon=True).start()
            try:
                receipt = json.loads(ready.get(timeout=12))
            except (Empty, ValueError, UnicodeError):
                return {"status": "BLOCKED", "reason": "startup_not_attested"}
            port = receipt.get("port")
            if receipt.get("ok") is not True or type(port) is not int or not 1024 <= port <= 65535:
                return {"status": "BLOCKED", "reason": "invalid_ephemeral_port"}
            auth = json.loads((state / "relay-auth.json").read_text("utf8"))
            token = auth.get("key")
            if not isinstance(token, str) or len(token) < 32:
                return {"status": "BLOCKED", "reason": "isolated_token_invalid"}

            def inference(i):
                return _request(port, token, "/v1/chat/completions", {
                    "model": "web-chatgpt-high",
                    "messages": [{"role": "user", "content": f"synthetic-{i}"}],
                })

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                requests = [pool.submit(inference, i) for i in range(3)]
                claimed = []
                deadline = time.monotonic() + 5
                while len(claimed) < 3 and time.monotonic() < deadline:
                    code, answer = _request(port, token, "/bridge/claim", {
                        "worker": str(uuid.uuid4())
                    })
                    if code != 200:
                        break
                    job = answer.get("job")
                    if job:
                        claimed.append(job)
                    else:
                        time.sleep(.03)
                if len(claimed) != 3:
                    return {
                        "status": "BLOCKED", "reason": "three_claims_unavailable",
                        "synthetic_jobs_claimed": len(claimed),
                    }
                unique = len({job["id"] for job in claimed}) == 3
                _, before = _request(port, token, "/bridge/status", None)
                actual = before.get("active_requests") == 3
                for i, job in enumerate(claimed):
                    url = f"https://chatgpt.com/c/synthetic-a4-test-{i}"
                    for action in ("intent", "saved", "result"):
                        code, _ = _request(port, token, "/bridge/event", {
                            "id": job["id"], "lease": job["lease"],
                            "action": action,
                            "tab": i + 101,
                            "url": url,
                            "text": json.dumps({
                                "request_id": job["id"],
                                "content": f"SYNTHETIC_FIXTURE_{i}",
                                "tool_calls": [],
                            })
                        })
                        if code != 200:
                            return {"status": "BLOCKED", "reason": "synthetic_event_rejected"}
                answers = [future.result(timeout=12) for future in requests]
                all_200 = all(code == 200 and
                              item.get("choices", [{}])[0].get("message", {}).get("content", "")
                              .startswith("SYNTHETIC_FIXTURE_")
                              for code, item in answers)
                _, after = _request(port, token, "/bridge/status", None)
                success = unique and actual and all_200 and after.get("active_requests") == 0
                return {
                    "status": "PASS" if success else "BLOCKED",
                    "synthetic_http_roundtrips": 3 if success else 0,
                    "three_concurrent_claims_verified": unique and actual,
                    "all_synthetic_responses_completed": all_200,
                    "test_isolated_ephemeral_port": True,
                    "actual_browser_workers_used": False,
                    "chatgpt_responses_verified": False,
                    "telegram_delivery_verified": False,
                }
        except (OSError, ValueError, KeyError, TypeError,
                concurrent.futures.TimeoutError):
            return {"status": "BLOCKED", "reason": "isolated_synthetic_runtime_failure"}
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
    result = isolated_three_requests()
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "PASS" else 2)
