"""Offline tests for the read-only Tunnel stack status command."""
from __future__ import annotations
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parent/"status-stack.py"
spec=importlib.util.spec_from_file_location("tunnel_stack_status",PATH)
status=importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_unconfigured_install_reports_missing_without_creating_files(self):
        before=list(self.root.rglob("*"))
        result=status.summary(self.root)
        self.assertEqual(result["mcp"]["status"],"source_missing")
        self.assertEqual(result["bridge"]["status"],"source_missing")
        self.assertFalse(result["full_autonomous_ready"])
        self.assertEqual(before,list(self.root.rglob("*")))

    def test_observer_auth_partial_secret_is_not_treated_as_ready(self):
        host=self.root/"Local-Workspace-Extensions"/"mcp-host-source"
        host.mkdir(parents=True)
        (host/"manage.py").write_text("# empty",encoding="utf-8")
        state=host/".local-mcp-state"
        state.mkdir()
        (state/"observer-token").write_text("A"*64,encoding="ascii")
        self.assertEqual(status._mcp_status(self.root,8765)["status"],
                         "local_auth_not_initialized")
        (state/"observer-authorization-header").write_text("Bearer "+"B"*64,encoding="ascii")
        self.assertEqual(status._mcp_status(self.root,8765)["status"],
                         "local_auth_invalid")

    def test_broken_extension_heartbeat_reported_without_secrets(self):
        p=self.root/"chat-launch-smoke-desktop-test"/"web-companion"/"bridge-state"/"diagnostics"
        p.mkdir(parents=True)
        (p/"extension.json").write_text('{"at":"not-a-date","token":"should-not-be-shown"}',encoding="utf-8")
        x=status._extension_status(self.root)
        self.assertEqual(x["status"],"invalid_heartbeat")
        self.assertNotIn("token",str(x))

    def test_future_heartbeat_does_not_claim_live_extension(self):
        p=self.root/"chat-launch-smoke-desktop-test"/"web-companion"/"bridge-state"/"diagnostics"
        p.mkdir(parents=True)
        (p/"extension.json").write_text(
            '{"at":"2099-01-01T00:00:00+00:00","version":"0.6.6"}',
            encoding="utf-8")
        self.assertEqual(status._extension_status(self.root)["status"],
                         "invalid_future_heartbeat")

    def test_unarmed_chain2_must_not_report_ready(self):
        def preview(path):
            if path.name=="chain_campaign.py":
                return {"ok":True,"armed":False,"held":False,
                        "ready":10,"states":{"done":0,"active":0}}
            return {"ok":True,"selected_mode":"trusted","effective_mode":"blocked",
                    "owner_armed":False,"chain2_fully_accepted":False}
        with patch.object(status,"_run_preview",side_effect=preview),\
             patch.object(status,"_mcp_status",return_value={"status":"ready"}),\
             patch.object(status,"_bridge_status",return_value={"status":"ready"}),\
             patch.object(status,"_extension_status",return_value={"status":"recent"}):
            x=status.summary(self.root)
        self.assertEqual(x["chain2"]["status"],"not_owner_armed")
        self.assertEqual(x["chain3"]["effective_mode"],"blocked")
        self.assertFalse(x["full_autonomous_ready"])
        self.assertTrue(any("P085" in y for y in x["next_actions"]))

    def test_chain2_triage_incidents_surface_as_read_only_actions(self):
        def preview(path):
            if path.name=="chain_campaign.py":
                return {"ok":True,"armed":True,"held":False,"ready":0,
                        "states":{"done":19,"active":9}}
            return {"ok":True,"selected_mode":"trusted","effective_mode":"blocked",
                    "owner_armed":False,"chain2_fully_accepted":False}
        categories={"browser_delivery_uncertain":6,"handoff_invalid":1,
                    "handoff_ready_for_role_review":1,"worker_evidence_incomplete":1,
                    "handoff_request_pending_ack":0}
        with (patch.object(status,"_run_preview",side_effect=preview),
              patch.object(status,"_chain2_triage_status",return_value={
                  "status":"needs_review","active":9,"categories":categories}),
              patch.object(status,"_mcp_status",return_value={"status":"ready"}),
              patch.object(status,"_bridge_status",return_value={"status":"ready"}),
              patch.object(status,"_extension_status",return_value={"status":"recent"})):
            result=status.summary(self.root)
        self.assertEqual(result["chain2"]["status"],"blocked_active")
        self.assertEqual(result["chain2_triage"]["active"],9)
        self.assertTrue(any("never replay" in a for a in result["next_actions"]))
        self.assertFalse(result["full_autonomous_ready"])

    def test_read_only_status_does_not_echo_local_bridge_key(self):
        p=self.root/"chat-launch-smoke-desktop-test"/"web-companion"
        p.mkdir(parents=True)
        (p/"bridge.py").write_text("# stub",encoding="utf-8")
        folder=p/"bridge-state"
        folder.mkdir()
        token="D"*64
        (folder/"token").write_text(token,encoding="utf-8")
        with patch.object(status.urllib.request,"urlopen",side_effect=ConnectionRefusedError):
            result=status._bridge_status(self.root,18763)
        self.assertEqual(result["status"],"not_running")
        self.assertNotIn(token,str(result))


    def test_mcp_malformed_health_json_is_unavailable_not_exception(self):
        host=self.root/"Local-Workspace-Extensions"/"mcp-host-source"
        host.mkdir(parents=True)
        (host/"manage.py").write_text("# stub",encoding="utf-8")
        state=host/".local-mcp-state"
        state.mkdir()
        token="A"*64
        (state/"observer-token").write_text(token,encoding="ascii")
        (state/"observer-authorization-header").write_text("Bearer "+token,encoding="ascii")
        bodies=("[]","null","false","42",
                '{"result":[]}', '{"result":{"serverInfo":[]}}',
                '{"result":{"serverInfo":null}}')
        for body in bodies:
            with self.subTest(body=body), patch.object(
                    status.urllib.request,"urlopen",return_value=io.StringIO(body)):
                response=status._mcp_status(self.root,8765)
            self.assertEqual(response,{"status":"unexpected_response","port":8765})
            self.assertNotIn(token,str(response))

    def test_bridge_malformed_health_json_is_not_ready(self):
        base=self.root/"chat-launch-smoke-desktop-test"/"web-companion"
        base.mkdir(parents=True)
        (base/"bridge.py").write_text("# stub",encoding="utf-8")
        state=base/"bridge-state"
        state.mkdir()
        token="D"*64
        (state/"token").write_text(token,encoding="utf-8")
        bodies=("[]","null","true","42",'"READY"',
                '{"ok":[]}', '{"ok":1}', '{"ok":"true"}')
        for body in bodies:
            with self.subTest(body=body), patch.object(
                    status.urllib.request,"urlopen",return_value=io.StringIO(body)):
                response=status._bridge_status(self.root,18763)
            self.assertEqual(response,{"status":"invalid_health","port":18763})
            self.assertNotIn(token,str(response))
        with patch.object(status.urllib.request,"urlopen",
                          return_value=io.StringIO('{"ok":true}')):
            self.assertEqual(status._bridge_status(self.root,18763),
                             {"status":"ready","port":18763})


if __name__=="__main__":
    unittest.main(verbosity=2)
