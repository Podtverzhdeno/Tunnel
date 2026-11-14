"""Persistent MCP CLIENT sidecar for Telegram -> Hermes -> Relay -> MCP -> Web CDP.

Hermes' own LLM request cannot invoke the tool that supplies its response.
This independent foreground process keeps one MCP stdio session open and
drains only Playwright-routed jobs from the existing Relay. No new gateway.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from hermes_web_live_cdp import validate_urls

ROOT = Path(__file__).resolve().parent
TOOL = "hermes_web_process_live_once"


class SidecarFault(RuntimeError):
    pass


def tool_receipt(reply) -> dict:
    content = getattr(reply, "content", None)
    if not isinstance(content, list) or len(content) != 1:
        raise SidecarFault("mcp_response_shape_invalid")
    if getattr(content[0], "type", None) != "text":
        raise SidecarFault("mcp_response_type_invalid")
    try:
        result = json.loads(content[0].text)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SidecarFault("mcp_response_json_invalid") from exc
    if not isinstance(result, dict):
        raise SidecarFault("mcp_response_not_object")
    return result


def classify_receipt(reply):
    item = tool_receipt(reply)
    if item.get("code") == "relay_job_not_available":
        return "idle"
    if item.get("outcome") == "done" and item.get("ok") is True:
        return "done"
    if item.get("outcome") in ("blocked", "uncertain") and item.get("ok") is False:
        return item["outcome"]
    # Don't spin forever if browser session/worker authentication breaks.
    raise SidecarFault("mcp_worker_outcome_unverified")


async def run(args, *, stop_after: int = 0):
    # Import only on execution; offline status/tests do not require MCP SDK.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    validate_urls(args.relay, args.cdp, args.chat)
    if not isinstance(args.journal, str) or not Path(args.journal).is_absolute():
        raise SidecarFault("absolute_journal_required")
    if not 30 <= args.timeout_seconds <= 900 or stop_after < 0:
        raise SidecarFault("timeout_or_limit_invalid")
    if os.environ.get("HERMES_CDP_LIVE_ENABLE") != "1":
        raise SidecarFault("live_opt_in_required")
    if not 32 <= len(os.environ.get("HERMES_CDP_LIVE_WORKER_KEY", "")) <= 256:
        raise SidecarFault("existing_relay_worker_principal_required")

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "hermes_web_cdp_mcp.py")],
        env=os.environ.copy()
    )
    parameters = {
        "relay_url": args.relay,
        "cdp_url": args.cdp,
        "chat_url": args.chat,
        "journal_dir": args.journal,
        "timeout_seconds": args.timeout_seconds,
    }
    count = 0
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            listed = await client.list_tools()
            if sum(t.name == TOOL for t in listed.tools) != 1:
                raise SidecarFault("live_mcp_tool_not_advertised")
            while stop_after == 0 or count < stop_after:
                reply = await client.call_tool(
                    TOOL, arguments=parameters,
                    read_timeout_seconds=timedelta(seconds=args.timeout_seconds + 45)
                )
                state = classify_receipt(reply)
                if state == "idle":
                    await asyncio.sleep(0.6)
                    continue
                count += 1
                # This stream contains no user prompts, replies or tokens.
                print(json.dumps({
                    "transport": "mcp_stdio_cdp", "extension_used": False,
                    "outcome": state, "processed": count
                }, separators=(",", ":")), flush=True)
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay", required=True)
    parser.add_argument("--cdp", required=True)
    parser.add_argument("--chat", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--stop-after", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args, stop_after=args.stop_after))
        return 0
    except (SidecarFault, KeyboardInterrupt):
        print('{"ok":false,"code":"mcp_sidecar_stopped"}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
