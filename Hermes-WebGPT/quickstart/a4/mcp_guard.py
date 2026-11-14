"""JSON-RPC MCP stdio guard — explicit tool-call boundary, fail-closed.

To become authoritative, Hermes must expose ONLY this guarded transport to the
agent. Never expose the unrestricted upstream through another connector/port.
No interactive browser auth is performed here.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .policy import AccessDenied, Policy

PASSTHROUGH = frozenset({"initialize", "ping", "notifications/initialized",
                         "notifications/cancelled"})


def _identity(request: dict[str, Any]) -> Any:
    return request.get("id")


class Guard:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.pending_lists: set[str] = set()
        # Correlate every forwarded request. Unsolicited backend replies must
        # never be allowed to forge an agent-visible tool result.
        self.pending_responses: set[str] = set()

    @staticmethod
    def _key(request_id: Any) -> str:
        return json.dumps(request_id, sort_keys=True)

    @staticmethod
    def deny(request: dict[str, Any], message: str = "permission_denied") -> dict[str, Any] | None:
        if "id" not in request:
            return None
        return {"jsonrpc": "2.0", "id": _identity(request),
                "error": {"code": -32003, "message": message}}

    def _track(self, message: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        if "id" in message:
            key = self._key(message["id"])
            if key in self.pending_responses:
                return False, self.deny(message, "duplicate_request_id")
            self.pending_responses.add(key)
        return True, None

    def request(self, message: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        """Return (may_forward, synthetic_response). Tracks authorized IDs."""
        if not isinstance(message, dict):
            return False, None
        method = message.get("method")
        if method == "tools/list":
            if "id" not in message:
                return False, None
            allowed, denial = self._track(message)
            if allowed:
                self.pending_lists.add(self._key(message["id"]))
            return allowed, denial
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                return False, self.deny(message)
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str):
                return False, self.deny(message)
            try:
                self.policy.check(name, arguments)
            except (AccessDenied, TypeError, KeyError, ValueError):
                return False, self.deny(message)
            return self._track(message)
        if method in PASSTHROUGH:
            return self._track(message)
        return False, self.deny(message, "method_not_supported")

    def response(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Sanitize backend responses before they reach an agent."""
        if not isinstance(message, dict):
            return None
        # Do not permit backend-initiated sampling, roots, or other requests.
        if "method" in message:
            method = message.get("method")
            return message if isinstance(method, str) and method.startswith("notifications/") else None
        if "id" not in message:
            return None
        key = self._key(message["id"])
        if key not in self.pending_responses:
            return None
        self.pending_responses.remove(key)
        if key in self.pending_lists:
            self.pending_lists.remove(key)
            result = message.get("result")
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                result = dict(result)
                allowed = []
                for tool in result["tools"]:
                    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                        continue
                    candidate_args = {"workspace": self.policy.workspace_alias,
                                      "path": str(self.policy.scope.relative_to(self.policy.workspace_root))}
                    if tool["name"].endswith("search_text"):
                        candidate_args["query"] = "probe"
                    try:
                        self.policy.check(tool["name"], candidate_args)
                    except (AccessDenied, TypeError, ValueError):
                        continue
                    allowed.append(tool)
                result["tools"] = allowed
                message = {**message, "result": result}
        return message


def serve(policy: Policy, backend: list[str]) -> int:
    """Run one trusted stdio MCP child; no shell and no request-argument logging."""
    if not backend:
        raise ValueError("missing_backend")
    process = subprocess.Popen(backend, shell=False, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1)
    assert process.stdin is not None and process.stdout is not None
    gate = Guard(policy)
    lock = threading.RLock()

    def emit(message):
        if message is None:
            return
        with lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def reader():
        for line in process.stdout:
            try:
                data = json.loads(line)
                with lock:
                    transformed = gate.response(data)
                    emit(transformed)
            except (ValueError, TypeError):
                # Invalid upstream frames are dropped, never echoed.
                continue

    background = threading.Thread(target=reader, daemon=True,
                                  name="mcp-a4-guard-reader")
    background.start()
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                with lock:
                    forward, synthetic = gate.request(message)
                    if synthetic:
                        emit(synthetic)
                    if forward:
                        if process.poll() is not None:
                            emit(Guard.deny(message, "backend_not_running"))
                            return 1
                        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                        process.stdin.flush()
            except (BrokenPipeError, OSError):
                return 1
            except (ValueError, TypeError):
                # Invalid JSON is not forwarded.
                continue
    finally:
        # EOF from the client is a normal graceful shutdown. Drain outstanding
        # JSON-RPC responses before killing a slow backend.
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        background.join(timeout=3)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("read_only", "full_access"), required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("backend", nargs=argparse.REMAINDER)
    opts = parser.parse_args()
    backend = opts.backend[1:] if opts.backend and opts.backend[0] == "--" else opts.backend
    policy = Policy(opts.mode, opts.workspace, opts.root, opts.scope)
    return serve(policy, backend)


if __name__ == "__main__":
    raise SystemExit(main())
