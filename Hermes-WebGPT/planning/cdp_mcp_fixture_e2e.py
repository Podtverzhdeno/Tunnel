"""True local Hermes Relay -> MCP tool -> Playwright browser fixture E2E.

No live Telegram, ChatGPT Web, browser extension or existing Relay state.
Run via the Playwright venv interpreter on Windows. Fails if browser missing.
"""
from __future__ import annotations
import concurrent.futures
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "planning"))
import relay
import hermes_web_cdp_mcp as mcp
from cdp_fixture_server import create_server


def post(url: str, key: str, value: dict) -> tuple[int, dict]:
    request = Request(url, method="POST",
                      data=json.dumps(value, ensure_ascii=False).encode("utf8"),
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=55) as response:
            return response.status, json.loads(response.read(650000))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read(650000))


def serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main():
    with ExitStack() as scope:
        temp = Path(scope.enter_context(tempfile.TemporaryDirectory(
            prefix="hermes-mcp-cdp-fixture-e2e-")))
        fixture = create_server()
        fixture_thread = serve(fixture)
        scope.callback(lambda: (fixture.shutdown(), fixture_thread.join(4),
                                fixture.server_close()))
        fixture_url = f"http://127.0.0.1:{fixture.server_address[1]}/"
        queue = relay.Queue(temp / "relay.sqlite3")
        scope.callback(queue.close)
        hermes_key = secrets.token_urlsafe(36)
        worker_key = secrets.token_urlsafe(36)
        service = relay.Server(("127.0.0.1", 0), queue, hermes_key,
                               enable_playwright=True,
                               playwright_key=worker_key, timeout=45)
        service.daemon_threads = True
        service_thread = serve(service)
        scope.callback(lambda: (service.shutdown(), service_thread.join(5),
                                service.server_close()))
        address = f"http://127.0.0.1:{service.server_address[1]}"
        # In this ONE synthetic test process, allow fixture URL as transcript
        # identity for saved/result. Production relay.chat_url is untouched.
        scope.enter_context(patch.object(relay, "chat_url",
                                         side_effect=lambda url: url == fixture_url))
        scope.enter_context(patch.dict(os.environ, {
            "HERMES_CDP_FIXTURE_ONLY": "1",
            "HERMES_CDP_FIXTURE_WORKER_KEY": worker_key,
        }))
        journal_dir = temp / "journal"
        journal_dir.mkdir()
        cases = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for case in ("ready", "challenge", "close_after_click"):
                payload = {"model": relay.MODEL_CDP,
                           "messages": [{"role": "user",
                                         "content": "MCP_LOCAL_FIXTURE_" + case}]}
                future = pool.submit(post, address + "/v1/chat/completions",
                                     hermes_key, payload)
                # The extension cannot claim a Playwright-routed job.
                negative_status, negative = post(address + "/bridge/claim",
                    hermes_key, {"worker": "ba2f5627-6df4-4ee9-bb4e-a4eebf683e6e"})
                assert negative_status == 200 and negative["job"] is None
                result = mcp.dispatch({
                    "jsonrpc": "2.0", "id": case,
                    "method": "tools/call",
                    "params": {
                        "name": mcp.TOOL_ONCE,
                        "arguments": {"relay_url": address,
                                      "fixture_url": fixture_url,
                                      "journal_dir": str(temp / "journal"),
                                      "case": case},
                    },
                })
                tool = result["result"]
                assert tool["isError"] is False, tool
                receipt = json.loads(tool["content"][0]["text"])
                code, body = future.result(timeout=55)
                expected = {"ready": ("done", 200, 1),
                            "challenge": ("blocked", 502, 0),
                            "close_after_click": ("uncertain", 502, 1)}[case]
                assert (receipt["outcome"], code, receipt["send_count"]) == expected, (
                    case, receipt, code)
                if case == "ready":
                    assert body["choices"][0]["message"]["content"] == "LOCAL_FIXTURE_OK"
                else:
                    assert body["error"]["code"].startswith("web_")
                cases.append({"case": case, "state": receipt["outcome"],
                              "http_status": code, "sends": receipt["send_count"],
                              "used_extension": False})
        print(json.dumps({
            "ok": True,
            "cases": cases,
            "hermes_openai_compatible_model": relay.MODEL_CDP,
            "relay": "production_handler_in_isolated_ephemeral_http_server",
            "mcp": "tools/call",
            "browser": "real_playwright_edge_local_fixture",
            "production_activated": False,
            "telegram_messages_sent": 0,
            "chatgpt_web_requests": 0,
        }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)
        where = tb[-1] if tb else None
        print(json.dumps({"ok": False, "exception_type": type(exc).__name__,
                          "source": Path(where.filename).name if where else None,
                          "source_line": where.lineno if where else None,
                          "reason": "mcp_fixture_e2e_failed"}, sort_keys=True))
        sys.exit(1)
