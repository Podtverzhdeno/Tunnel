"""MCP stdio contract tests: no Telegram, extensions or real ChatGPT."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import hermes_web_cdp_mcp as bridge


class McpContractTests(unittest.TestCase):
    def call(self, method, params=None, ident=1):
        return bridge.dispatch({"jsonrpc": "2.0", "id": ident,
                                "method": method, "params": params or {}})

    def test_initialization_and_tool_discovery(self):
        initialized = self.call("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", initialized["result"]["capabilities"])
        offered = self.call("tools/list")["result"]["tools"]
        self.assertEqual({x["name"] for x in offered}, {
            bridge.TOOL_STATUS, bridge.TOOL_ONCE,
        })
        self.assertTrue(offered[0]["annotations"]["readOnlyHint"])
        self.assertFalse(offered[1]["annotations"]["readOnlyHint"])

    def test_status_makes_no_claim_or_unverified_live_success(self):
        result = self.call("tools/call", {
            "name": bridge.TOOL_STATUS, "arguments": {},
        })
        state = json.loads(result["result"]["content"][0]["text"])
        self.assertFalse(result["result"]["isError"])
        self.assertFalse(state["extension_required"])
        self.assertTrue(state["relay_reused"])
        self.assertFalse(state["live_chatgpt_mcp_inbound"])
        self.assertFalse(state["live_chatgpt_automation_enabled"])

    def test_rejects_live_chatgpt_and_external_urls_before_claim(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            "HERMES_CDP_FIXTURE_ONLY": "1",
            "HERMES_CDP_FIXTURE_WORKER_KEY": "x" * 48,
        }):
            base = {"relay_url": "http://127.0.0.1:18765",
                    "fixture_url": "http://127.0.0.1:54321/",
                    "journal_dir": root, "case": "ready"}
            for overrides in (
                {"fixture_url": "https://chatgpt.com/"},
                {"fixture_url": "http://localhost:54321/"},
                {"relay_url": "http://evil.example:18765"},
                {"fixture_url": "http://127.0.0.1:54321/a/../"},
                {"case": "LIVE"},
            ):
                item = dict(base, **overrides)
                with self.subTest(overrides=overrides):
                    with self.assertRaises(bridge.BridgeFault):
                        bridge.validate_once(item)

    def test_gate_and_key_required(self):
        with tempfile.TemporaryDirectory() as root:
            args = {"relay_url": "http://127.0.0.1:18765",
                    "fixture_url": "http://127.0.0.1:54321/",
                    "journal_dir": root, "case": "ready"}
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(bridge.BridgeFault, "fixture_gate"):
                    bridge.validate_once(args)
            with patch.dict(os.environ, {"HERMES_CDP_FIXTURE_ONLY": "1"}, clear=True):
                with self.assertRaisesRegex(bridge.BridgeFault, "worker_key_missing"):
                    bridge.validate_once(args)

    def test_mcp_failed_tool_does_not_leak_arguments(self):
        secret = "confidential-message-do-not-log"
        result = self.call("tools/call", {
            "name": bridge.TOOL_ONCE,
            "arguments": {"prompt": secret, "case": "ready"},
        })
        raw = json.dumps(result)
        self.assertNotIn(secret, raw)
        self.assertTrue(result["result"]["isError"])

    def test_stdio_multimessage_framing_and_notifications(self):
        source = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": 4,
                        "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 5}),
            json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 6,
                        "params": {"name": bridge.TOOL_STATUS, "arguments": {}}}),
            "",
        ]))
        output = io.StringIO()
        bridge.serve(source, output)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([x["id"] for x in messages], [4, 5, 6])
        self.assertEqual(len(messages[1]["result"]["tools"]), 2)

    def test_real_stdio_process_has_exact_mcp_frames(self):
        request = chr(10).join([
            json.dumps({"jsonrpc":"2.0","id":101,"method":"initialize",
                        "params":{"protocolVersion":"2025-06-18"}}),
            json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}),
            json.dumps({"jsonrpc":"2.0","id":102,"method":"tools/list"}),
            json.dumps({"jsonrpc":"2.0","id":103,"method":"tools/call",
                        "params":{"name":bridge.TOOL_STATUS,"arguments":{}}}),
            "",
        ])
        result = subprocess.run(
            [sys.executable, str(Path(bridge.__file__).resolve())],
            input=request, capture_output=True, text=True, timeout=10,
            encoding="utf8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        self.assertFalse(result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([frame["id"] for frame in frames], [101,102,103])
        self.assertEqual(len(frames[1]["result"]["tools"]), 2)

    def test_malformed_initialize_parameters_are_not_fatal(self):
        for params in ([], None, 1, "unexpected"):
            with self.subTest(params=params):
                response = bridge.dispatch({"jsonrpc": "2.0", "id": 1,
                                            "method": "initialize", "params": params})
                self.assertEqual(response["error"]["code"], -32602)
        response = self.call("initialize", {"protocolVersion": {"bad": True}})
        self.assertEqual(response["result"]["protocolVersion"], bridge.MCP_VERSION)

    def test_unknown_method_returns_jsonrpc_error(self):
        self.assertEqual(self.call("unrecognized")["error"]["code"], -32601)
        self.assertIsNone(bridge.dispatch({"jsonrpc": "2.0",
                                           "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
