"""Real MCP SDK stdio handshake with the extension-free Hermes bridge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

SERVER = Path(__file__).resolve().parents[1] / "hermes_web_cdp_mcp.py"


async def run():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print(json.dumps({"ok": False, "reason": "mcp_sdk_not_installed"}))
        return 2
    params=StdioServerParameters(command=sys.executable, args=[str(SERVER)], env={})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            ready=await client.initialize()
            offered=await client.list_tools()
            names={t.name for t in offered.tools}
            assert names == {"hermes_web_transport_status",
                             "hermes_web_process_fixture_once"}, names
            status=await client.call_tool("hermes_web_transport_status", {})
            assert not status.isError
            item=json.loads(status.content[0].text)
            assert item["relay_reused"] and not item["extension_required"]
            assert not item["live_chatgpt_automation_enabled"]
            print(json.dumps({"ok": True, "sdk": "mcp.ClientSession",
                              "tools_discovered": sorted(names),
                              "browser_extension_required": False,
                              "live_chatgpt_requests": 0}))
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except Exception as exc:
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,
                          "reason":"mcp_client_handshake_failed"}))
        raise SystemExit(1)
