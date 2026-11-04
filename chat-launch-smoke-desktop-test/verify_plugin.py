"""Run MCP stdio wire tests on the user's actual Windows host; no ChatGPT requests."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
URI = "ui://chat-launch-smoke/panel.html"

def request(method, params=None, ident=1):
    return {"jsonrpc":"2.0","id":ident,"method":method,"params":params or {}}

def wire(*requests):
    raw="".join(json.dumps(r,ensure_ascii=False)+"\n" for r in requests)
    run=subprocess.run([sys.executable,str(BASE/"server.py")],input=raw,text=True,
                       encoding="utf-8",errors="replace",capture_output=True,timeout=15,cwd=BASE)
    if run.returncode: raise AssertionError("server exited %s, stderr=%s"%(run.returncode,run.stderr[:400]))
    return [json.loads(s) for s in run.stdout.splitlines() if s.strip()]

class WireTests(unittest.TestCase):
    def test_initialize(self):
        x=wire(request("initialize",{"protocolVersion":"2025-11-25"}))[0]
        self.assertEqual(x["result"]["protocolVersion"],"2025-11-25")
        self.assertIn("tools",x["result"]["capabilities"])
        self.assertIn("resources",x["result"]["capabilities"])
    def test_ping(self):
        self.assertEqual(wire(request("ping"))[0]["result"],{})
    def test_tool_is_ui_app(self):
        t=wire(request("tools/list"))[0]["result"]["tools"]
        self.assertEqual(len(t),1)
        self.assertEqual(t[0]["name"],"show_new_chat_test")
        self.assertEqual(t[0]["_meta"]["ui"]["resourceUri"],URI)
        self.assertEqual(t[0]["_meta"]["openai/ui"]["entrypoints"][0]["type"],"global")
    def test_tool_call_harmless(self):
        r=wire(request("tools/call",{"name":"show_new_chat_test","arguments":{}}))[0]
        self.assertIn("Панель теста готова",r["result"]["content"][0]["text"])
    def test_resource(self):
        r=wire(request("resources/read",{"uri":URI}))[0]["result"]["contents"][0]
        self.assertEqual(r["mimeType"],"text/html;profile=mcp-app")
        self.assertIn("ui/message",r["text"])
        self.assertIn("target:'new',send:true",r["text"])
    def test_unknown_uri_is_rejected(self):
        r=wire(request("resources/read",{"uri":"ui://no"}))[0]
        self.assertIn("error",r)
    def test_notifications_have_no_responses(self):
        n={"jsonrpc":"2.0","method":"notifications/initialized"}
        self.assertEqual(wire(n),[])
    def test_multiple_wire_requests(self):
        r=wire(request("ping",ident=4),request("tools/list",ident=5))
        self.assertEqual([x["id"] for x in r],[4,5])
    def test_manifest_portable(self):
        m=json.loads((BASE/"plugin.json").read_text(encoding="utf-8"))
        conf=json.loads((BASE/"mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(m["name"],"chat-launch-smoke")
        self.assertEqual(conf["mcpServers"]["chat-launch-smoke"]["type"],"stdio")
        self.assertEqual(conf["mcpServers"]["chat-launch-smoke"]["command"],"python")

if __name__=="__main__":
    unittest.main(verbosity=2)
