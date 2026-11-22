"""MCP sidecar contract: status, tool dispatch, idle, done and unknown.

No live CDP browser or network. All results are synthetic.
"""
from __future__ import annotations
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hermes_web_cdp_mcp as server
from hermes_cdp_mcp_sidecar import classify_receipt, SidecarFault
from hermes_cdp_relay_client import WorkerFault


def mock_reply(content):
    import json
    return SimpleNamespace(content=[
        SimpleNamespace(type="text", text=json.dumps(content))
    ])


class SidecarContractTests(unittest.TestCase):
    def test_normal_and_idle_classification(self):
        for item, expected in [
            ({"ok": False, "code": "relay_job_not_available"}, "idle"),
            ({"ok": True, "outcome": "done"}, "done"),
            ({"ok": False, "outcome": "blocked"}, "blocked"),
            ({"ok": False, "outcome": "uncertain"}, "uncertain"),
        ]:
            self.assertEqual(classify_receipt(mock_reply(item)), expected)
        for item in ({"ok": False, "code": "worker_failed"},
                     {"ok": True, "outcome": "uncertain"}, []):
            with self.assertRaises(SidecarFault):
                classify_receipt(mock_reply(item))

    def test_live_not_advertised_until_opt_in(self):
        with patch.dict("os.environ", {"HERMES_CDP_LIVE_ENABLE": "0"}):
            listed = server.dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list"
            })
            self.assertNotIn(server.TOOL_LIVE,
                             [x["name"] for x in listed["result"]["tools"]])
        with patch.dict("os.environ", {"HERMES_CDP_LIVE_ENABLE": "1"}):
            listed = server.dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list"
            })
            self.assertIn(server.TOOL_LIVE,
                          [x["name"] for x in listed["result"]["tools"]])

    def test_idle_mcp_tool_returns_distinct_no_work_receipt(self):
        async def no_job(*_args, **_kwargs):
            raise WorkerFault("relay_job_not_available")
        with patch.dict("os.environ", {"HERMES_CDP_LIVE_ENABLE": "1",
                                       "HERMES_CDP_LIVE_WORKER_KEY": "a"*45}), \
             patch("hermes_web_live_cdp.run_once", side_effect=no_job):
            response = server.dispatch({
                "jsonrpc": "2.0", "id": 99, "method": "tools/call",
                "params": {"name": server.TOOL_LIVE, "arguments": {
                    "relay_url": "http://127.0.0.1:18765",
                    "cdp_url": "http://127.0.0.1:9223",
                    "chat_url": "https://chatgpt.com/c/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                    "journal_dir": "C:\\Users\\test\\Hermes-WebCDP\\journal"
                }}
            })
        import json
        text = response["result"]["content"][0]["text"]
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(json.loads(text)["code"], "relay_job_not_available")


if __name__ == "__main__":
    unittest.main()
