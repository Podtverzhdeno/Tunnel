"""Hermes MCP -> existing Relay Playwright route, without Web Companion.

The *only* runnable browser target here is the local deterministic CDP fixture.
Real ChatGPT Web has no inbound MCP conversation API; this integration does not
claim or perform unattended output extraction from chatgpt.com.

MCP stdio: use with Hermes Agent's `mcp_servers` configuration. The mutation
tool claims a single existing Relay job and reuses the verified one-send worker.
No second Telegram gateway, model server, relay SQLite or browser extension.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
WORKER_DIR = ROOT / "planning" / "cdp-live-preflight-20261111" / "standalone-worker-v2"
PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
MCP_VERSION = "2025-06-18"
MAX_LINE = 256_000
TOOL_STATUS = "hermes_web_transport_status"
TOOL_ONCE = "hermes_web_process_fixture_once"
MAX_JOURNAL_PATH = 512

class BridgeFault(ValueError):
    pass

def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": TOOL_STATUS,
            "description": "Describe this extension-free Hermes MCP transport and whether the local fixture gate is enabled. Does not claim a relay job.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": TOOL_ONCE,
            "description": "Claim exactly one existing Hermes Relay playwright job and run its local synthetic browser fixture. Never targets ChatGPT Web or sends Telegram messages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "relay_url": {"type": "string", "description": "http://127.0.0.1:<port> of the existing test relay"},
                    "fixture_url": {"type": "string", "description": "http://127.0.0.1:<port>/ of the local fixture"},
                    "journal_dir": {"type": "string", "description": "Dedicated absolute directory for the one-send journal"},
                    "case": {"type": "string", "enum": ["ready", "challenge", "duplicate_send", "wrong_chat", "close_before_click", "close_after_click", "missing_composer"]},
                },
                "required": ["relay_url", "fixture_url", "journal_dir", "case"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "openWorldHint": False, "destructiveHint": False},
        },
    ]

def status() -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": "hermes-web-cdp-mcp-fixture-v1",
        "transport": "playwright",
        "relay_reused": True,
        "extension_required": False,
        "live_chatgpt_mcp_inbound": False,
        "live_chatgpt_automation_enabled": False,
        "fixture_gate_enabled": os.environ.get("HERMES_CDP_FIXTURE_ONLY") == "1",
        "worker_key_configured": bool(os.environ.get("HERMES_CDP_FIXTURE_WORKER_KEY")),
    }

def validate_once(args: dict[str, Any]) -> dict[str, str]:
    if not isinstance(args, dict) or set(args) != {"relay_url", "fixture_url", "journal_dir", "case"}:
        raise BridgeFault("fixture_parameters_invalid")
    if os.environ.get("HERMES_CDP_FIXTURE_ONLY") != "1":
        raise BridgeFault("fixture_gate_required")
    key = os.environ.get("HERMES_CDP_FIXTURE_WORKER_KEY", "")
    if not 32 <= len(key) <= 256:
        raise BridgeFault("fixture_worker_key_missing")
    if any(not isinstance(x, str) or not x for x in args.values()):
        raise BridgeFault("fixture_parameter_type_invalid")
    if len(args["journal_dir"]) > MAX_JOURNAL_PATH:
        raise BridgeFault("journal_path_too_long")
    journal = Path(args["journal_dir"])
    if not journal.is_absolute() or journal.is_symlink():
        raise BridgeFault("private_absolute_journal_required")
    if args["case"] not in tools()[1]["inputSchema"]["properties"]["case"]["enum"]:
        raise BridgeFault("unsupported_fixture_case")
    # Validate URLs before importing browser dependencies or claiming a job.
    from urllib.parse import urlsplit
    relay, fixture = args["relay_url"], args["fixture_url"]
    for raw, allow_path in ((relay, {"", "/"}), (fixture, {"", "/", "/index.html"})):
        try:
            u = urlsplit(raw)
            port = u.port
        except (ValueError, TypeError) as exc:
            raise BridgeFault("invalid_loopback_url") from exc
        if (u.scheme != "http" or u.hostname != "127.0.0.1" or
                port is None or not (1 <= port <= 65535) or
                u.netloc != f"127.0.0.1:{port}" or u.path not in allow_path or
                u.query or u.fragment or u.username or u.password):
            raise BridgeFault("only_local_fixture_and_relay")
    return args

async def process_fixture_once(args: dict[str, Any]) -> dict[str, Any]:
    validate_once(args)
    if str(WORKER_DIR) not in sys.path:
        sys.path.insert(0, str(WORKER_DIR))
    # The existing audited fixture worker handles claim, page provenance,
    # before-click journal, one Send, saved/result ACK and uncertain recovery.
    import fixture_worker as worker
    worker.verify_fixture_health(args["fixture_url"])
    http = worker.ScopedWorkerHttp(args["relay_url"],
                                  os.environ["HERMES_CDP_FIXTURE_WORKER_KEY"])
    journal = worker.SendJournal(args["journal_dir"])
    result = await worker.execute_one(http, args["fixture_url"], args["case"], journal)
    return {
        "ok": result.get("outcome") in {"done", "blocked", "uncertain"},
        "fixture_only": True,
        "browser_extension_used": False,
        "live_chatgpt_requests": 0,
        "telegram_messages_sent": 0,
        **result,
    }

def _result(ident: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": value}

def _error(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}

def _tool_result(value: dict[str, Any], *, failed: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
        "isError": failed,
    }

def dispatch(request: Any) -> dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid_request")
    method = request.get("method")
    ident = request.get("id")
    if method == "notifications/initialized" or (
        isinstance(method, str) and method.startswith("notifications/")
    ):
        return None
    if "id" not in request or isinstance(ident, (dict, list, bool)):
        return _error(None, -32600, "invalid_request")
    if method == "initialize":
        requested = request.get("params", {}).get("protocolVersion", MCP_VERSION)
        chosen = requested if requested in PROTOCOLS else MCP_VERSION
        return _result(ident, {
            "protocolVersion": chosen,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "hermes-web-cdp-mcp-fixture", "version": "0.1.0"},
        })
    if method == "ping":
        return _result(ident, {})
    if method == "tools/list":
        return _result(ident, {"tools": tools()})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return _error(ident, -32602, "invalid_params")
        name, args = params.get("name"), params.get("arguments", {})
        if name == TOOL_STATUS:
            if args != {}:
                return _result(ident, _tool_result({"ok": False, "code": "status_args_invalid"}, failed=True))
            return _result(ident, _tool_result(status()))
        if name == TOOL_ONCE:
            try:
                return _result(ident, _tool_result(asyncio.run(process_fixture_once(args))))
            except Exception as exc:
                # Fail-closed, avoid leaking tokens, prompts or local filenames.
                code = str(exc) if isinstance(exc, BridgeFault) else "fixture_execution_failed"
                return _result(ident, _tool_result({"ok": False, "code": code}, failed=True))
        return _error(ident, -32602, "unknown_tool")
    return _error(ident, -32601, "method_not_found")

def serve(stdin=None, stdout=None) -> None:
    source = sys.stdin if stdin is None else stdin
    output = sys.stdout if stdout is None else stdout
    for line in source:
        if len(line) > MAX_LINE:
            output.write(json.dumps(_error(None, -32600, "frame_too_large")) + "\n")
            output.flush()
            continue
        try:
            request = json.loads(line)
        except (ValueError, TypeError):
            response = _error(None, -32700, "parse_error")
        else:
            response = dispatch(request)
        if response is not None:
            output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()

if __name__ == "__main__":
    serve()
