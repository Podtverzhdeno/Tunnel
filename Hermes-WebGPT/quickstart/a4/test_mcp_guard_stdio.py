"""Real subprocess/stdio regression for the fail-closed JSON-RPC guard."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
MOCK = r'''import json,sys
for line in sys.stdin:
    x=json.loads(line)
    method=x.get("method")
    if method=="tools/list":
        y={"jsonrpc":"2.0","id":x["id"],"result":{"tools":[
            {"name":"read_file"},{"name":"write_file"},{"name":"run_command"}]}}
    elif method=="tools/call":
        y={"jsonrpc":"2.0","id":x["id"],"result":{"forwarded":x["params"]["name"]}}
    else:
        y={"jsonrpc":"2.0","id":x.get("id"),"result":{}}
    sys.stdout.write(json.dumps(y)+"\n")
    sys.stdout.flush()
'''


class StdioIntegrationTests(unittest.TestCase):
    def run_guard(self, mode, requests):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "allowed").mkdir()
            mock = root / "mock_backend.py"
            mock.write_text(MOCK, encoding="utf-8")
            command = [sys.executable, "-m", "quickstart.a4.mcp_guard",
                       "--mode", mode, "--workspace", "projects",
                       "--root", str(root), "--scope", str(root / "allowed"),
                       "--", sys.executable, str(mock)]
            proc = subprocess.run(command, cwd=PROJECT,
                                  input="\n".join(json.dumps(x) for x in requests) + "\n",
                                  capture_output=True, encoding="utf-8",
                                  timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return [json.loads(line) for line in proc.stdout.splitlines() if line]

    def test_read_only_filters_and_blocks_real_backend_calls(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "write_file", "arguments": {
                 "workspace": "projects", "path": "allowed/f", "content": "SECRET-DO-NOT-LOG"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "read_file", "arguments": {
                 "workspace": "projects", "path": "allowed/f"}}},
        ]
        output = self.run_guard("read_only", requests)
        messages = {o.get("id"): o for o in output}
        self.assertEqual([x["name"] for x in messages[1]["result"]["tools"]], ["read_file"])
        self.assertEqual(messages[2]["error"]["code"], -32003)
        self.assertEqual(messages[3]["result"]["forwarded"], "read_file")
        self.assertNotIn("SECRET-DO-NOT-LOG", json.dumps(output))

    def test_scoped_write_reaches_backend_only_in_full_mode(self):
        item = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "write_file", "arguments": {
                    "workspace": "projects", "path": "allowed/f", "content": "test"}}}
        run = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "run_command", "arguments": {
                   "workspace": "projects", "path": "allowed"}}}
        output = self.run_guard("full_access", [item, run])
        messages = {o.get("id"): o for o in output}
        self.assertEqual(messages[1]["result"]["forwarded"], "write_file")
        self.assertEqual(messages[2]["error"]["code"], -32003)


if __name__ == "__main__":
    unittest.main()
