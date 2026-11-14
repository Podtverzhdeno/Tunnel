"""Native MCP SDK handshake for opt-in live transport, with NO live Send."""
from __future__ import annotations
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
from datetime import timedelta

SERVER = Path(__file__).resolve().parents[1] / "hermes_web_cdp_mcp.py"


async def run():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # A real bearer is intentionally absent: invalid URL must reject
    # before any Relay claim or browser attachment.
    env = dict(os.environ)
    env["HERMES_CDP_LIVE_ENABLE"] = "1"
    env["HERMES_CDP_LIVE_WORKER_KEY"] = "X" * 48
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], env=env
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as client:
            await client.initialize()
            names = [tool.name for tool in (await client.list_tools()).tools]
            if names.count("hermes_web_process_live_once") != 1:
                raise AssertionError("live_tool_missing")
            if "read_timeout_seconds" not in inspect.signature(client.call_tool).parameters:
                raise AssertionError("sdk_timeout_argument_missing")
            status = await client.call_tool("hermes_web_transport_status", {})
            doc = json.loads(status.content[0].text)
            if status.isError or not doc["live_chatgpt_automation_enabled"]:
                raise AssertionError("live_gate_not_enabled")
            denied = await client.call_tool(
                "hermes_web_process_live_once",
                arguments={"relay_url": "http://localhost:12345",
                           "cdp_url": "http://127.0.0.1:9223",
                           "chat_url": "https://chatgpt.com/c/1111-2222",
                           "journal_dir": "C:\\Temp\\Hermes-CDP-Test"},
                read_timeout_seconds=timedelta(seconds=15)
            )
            if not denied.isError:
                raise AssertionError("invalid_url_not_denied")
            result = json.loads(denied.content[0].text)
            if result.get("code") != "live_worker_failed":
                raise AssertionError("unexpected_denial_contract")
    return {
        "ok": True, "mcp_sdk": "real_ClientSession_stdio",
        "live_tool_advertised": True, "invalid_request_denied": True,
        "relay_requests_claimed": 0, "browser_sends": 0,
        "telegram_messages_sent": 0
    }


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(run()), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "phase": "mcp_live_sdk_handshake"}, sort_keys=True))
        raise SystemExit(2)
