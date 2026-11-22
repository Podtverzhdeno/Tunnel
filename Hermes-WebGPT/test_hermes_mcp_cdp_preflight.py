"""Read-only direct MCP/CDP readiness; synthetic HTTP metadata only."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hermes_mcp_cdp_preflight as health


class CDPReadinessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hermes-preflight-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/"data").mkdir()
        (self.root/"data"/"relay-auth.json").write_text(
            json.dumps({"key":"synthetic-local-token-not-production"}),
            encoding="utf8"
        )
        (self.root/"data"/"playwright-worker-auth.json").write_text(
            json.dumps({"key":"synthetic-worker-key"}),encoding="utf8"
        )

    def test_ready_metadata_never_claims_authenticated_roundtrip(self):
        answers={
            "/health":{"service":"hermes-webgpt-relay","protocol":1},
            "/v1/models":{"data":[{"id":"web-chatgpt-high-cdp"}]},
            "/json/version":{"Browser":"Yandex/26"},
            "/json/list":[{"type":"page",
                           "url":"https://chatgpt.com/c/1111-2222"},
                          {"type":"page","url":"https://evil.test/"}]
        }
        def fake_fetch(url,bearer=None):
            for path,doc in answers.items():
                if url.endswith(path):
                    return doc
            raise ValueError("unrecognized_probe")
        with patch.object(health,"ROOT",self.root), patch.object(health,"fetch",side_effect=fake_fetch):
            report=health.inspect()
        self.assertTrue(report["relay_reachable"])
        self.assertTrue(report["playwright_model_enabled"])
        self.assertTrue(report["cdp_reachable"])
        self.assertEqual(report["matching_chat_tabs"],1)
        self.assertTrue(report["worker_principal_present"])
        self.assertFalse(report["browser_authentication_verified"])
        self.assertFalse(report["ready_for_verified_e2e"])
        self.assertEqual(report["actions"],0)
        self.assertNotIn("synthetic",json.dumps(report))

    def test_unavailable_sources_remain_unverified(self):
        with patch.object(health,"ROOT",self.root), \
             patch.object(health,"fetch",side_effect=OSError):
            report=health.inspect()
        self.assertFalse(report["relay_reachable"])
        self.assertFalse(report["ready_for_verified_e2e"])
        self.assertEqual(report["actions"],0)


if __name__ == "__main__":
    unittest.main()
