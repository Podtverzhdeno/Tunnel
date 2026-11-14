"""Protocol-level authorization tests without launching a real MCP host."""
import tempfile
import unittest
from pathlib import Path
from quickstart.a4.mcp_guard import Guard
from quickstart.a4.policy import Policy

class GuardTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        (root / "allowed").mkdir()
        self.policy = Policy("read_only", "projects", root, root / "allowed")

    def call(self, name, **kw):
        return {"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                "params": {"name": name, "arguments": {"workspace": "projects", **kw}}}

    def test_denial_never_reaches_backend(self):
        guard = Guard(self.policy)
        for name in ("write_file", "run_command", "git_push", "unknown"):
            forward, response = guard.request(self.call(name, path="allowed/file"))
            self.assertFalse(forward)
            self.assertEqual(response["error"]["code"], -32003)

    def test_valid_read_forwarded(self):
        guard = Guard(self.policy)
        forward, error = guard.request(self.call("read_file", path="allowed/file"))
        self.assertTrue(forward)
        self.assertIsNone(error)

    def test_tool_list_is_filtered(self):
        guard = Guard(self.policy)
        forward, _ = guard.request({"jsonrpc": "2.0", "id": 100, "method": "tools/list"})
        self.assertTrue(forward)
        response = guard.response({
            "jsonrpc": "2.0", "id": 100, "result": {
                "tools": [{"name": "read_file"}, {"name": "write_file"},
                          {"name": "run_command"}, {"name": "search_text"}]}})
        names = [t["name"] for t in response["result"]["tools"]]
        self.assertEqual(names, ["read_file", "search_text"])
        self.assertEqual(guard.pending_lists, set())

    def test_backend_cannot_request_client_sampling(self):
        guard = Guard(self.policy)
        self.assertIsNone(guard.response({
            "jsonrpc": "2.0", "id": 1, "method": "sampling/createMessage"}))

    def test_unknown_method_refused(self):
        guard = Guard(self.policy)
        forward, response = guard.request({
            "jsonrpc": "2.0", "id": 9, "method": "resources/read"})
        self.assertFalse(forward)
        self.assertEqual(response["error"]["message"], "method_not_supported")

    def test_read_tool_outside_scope_refused(self):
        guard = Guard(self.policy)
        forward, response = guard.request(self.call("read_file", path="other/path"))
        self.assertFalse(forward)
        self.assertEqual(response["error"]["message"], "permission_denied")

    def test_full_access_still_denies_unrestricted_execution(self):
        policy = Policy("full_access", self.policy.workspace_alias,
                        self.policy.workspace_root, self.policy.scope)
        guard = Guard(policy)
        forward, _ = guard.request(self.call("write_file", path="allowed/f.txt"))
        self.assertTrue(forward)
        forward, response = guard.request(self.call("run_script", path="allowed/f.py"))
        self.assertFalse(forward)
        self.assertEqual(response["error"]["code"], -32003)


if __name__ == "__main__":
    unittest.main()
