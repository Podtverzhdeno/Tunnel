"""A4 actual read-only MCP boot tests; use synthetic temp config + ephemeral port."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from quickstart.a4.observer_runtime import (
    audit_readonly, locate_native_host, local_port_free, start, EXPECTED,
    ObserverStartupError,
)

PROJECT = Path(__file__).resolve().parents[2]


class ObserverRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspaces"
        self.project = self.workspace / "Hermes-WebGPT"
        self.project.mkdir(parents=True)
        browser = self.workspace / "YandexBrowser" / "Application" / "browser.exe"
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"synthetic")
        self.state = self.base / "state"
        self.state.mkdir()
        self.settings = self.state / "settings-a4.json"
        self.base_cfg = {
            "schema_version":1, "mode":"read_only", "workers":3,
            "browser":"yandex","browser_exe":str(browser),
            "scope":str(self.project),
            "enforcement":"pending_backend_integration",
        }
        self.settings.write_text(json.dumps(self.base_cfg), encoding="utf-8")
        self.token = "A" * 60

    def test_audit_list_exact_six_only(self):
        def rpc(port, token, request):
            self.assertEqual(token, self.token)
            return {"result":{"tools":[{"name":n} for n in sorted(EXPECTED)]}}
        result = audit_readonly(18766, self.token, rpc=rpc)
        self.assertEqual(result["tool_count"],6)
        self.assertFalse(result["tunnel_connected"])

    def test_audit_refuses_extraneous_tool(self):
        def rpc(port, token, request):
            return {"result":{"tools":[{"name":n} for n in sorted(EXPECTED | {"write_file"})]}}
        with self.assertRaisesRegex(ObserverStartupError,"inventory"):
            audit_readonly(18766, self.token, rpc=rpc)

    def test_full_access_cannot_start_observer(self):
        cfg = dict(self.base_cfg, mode="full_access")
        self.settings.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(ObserverStartupError):
            start(project=self.project, settings_path=self.settings,
                  state=self.state, token=self.token, port=self.free_port(),
                  host=self.state)
        self.assertFalse((self.state / "a4-observer-config.json").exists())

    def test_missing_token_refuses_to_launch(self):
        with self.assertRaises(ObserverStartupError):
            start(project=self.project, settings_path=self.settings,
                  state=self.state, token=None, port=self.free_port())

    def free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1",0))
            return sock.getsockname()[1]

    def test_port_collision_fails_closed(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1",0))
            sock.listen(1)
            port = sock.getsockname()[1]
            self.assertFalse(local_port_free(port))
            with self.assertRaisesRegex(ObserverStartupError,"occupied"):
                start(project=self.project, settings_path=self.settings,
                      state=self.state, token=self.token, port=port,
                      host=locate_native_host(PROJECT))

    def test_dead_receipt_is_archived_for_reboot_recovery(self):
        from quickstart.a4.observer_runtime import _archive_dead_receipt
        receipt = self.state / "a4-observer-launch.json"
        prior = {"pid": 43123, "port": 18766, "profile": "observer"}
        receipt.write_text(json.dumps(prior), encoding="utf-8")
        _archive_dead_receipt(self.state, receipt, prior,
                              is_present=lambda pid: False)
        self.assertFalse(receipt.exists())
        archived = self.state / "a4-observer-launch.dead-43123.json"
        self.assertEqual(json.loads(archived.read_text(encoding="utf-8")),
                         prior)

    def test_live_or_unknown_pid_blocks_reboot_recovery(self):
        from quickstart.a4.observer_runtime import (_archive_dead_receipt,
                                                      ObserverStartupError)
        receipt = self.state / "a4-observer-launch.json"
        prior = {"pid": 43123, "port": 18766, "profile": "observer"}
        receipt.write_text(json.dumps(prior), encoding="utf-8")
        for state in (True, None):
            with self.assertRaisesRegex(ObserverStartupError, "still_present_or_unknown"):
                _archive_dead_receipt(self.state, receipt, prior,
                                      is_present=lambda pid: state)
            self.assertTrue(receipt.exists())

    def test_recovery_never_overwrites_existing_archive(self):
        from quickstart.a4.observer_runtime import _archive_dead_receipt
        receipt = self.state / "a4-observer-launch.json"
        prior = {"pid": 43123, "port": 18766, "profile": "observer"}
        receipt.write_text(json.dumps(prior), encoding="utf-8")
        archived = self.state / "a4-observer-launch.dead-43123.json"
        archived.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ObserverStartupError,"archive_collision"):
            _archive_dead_receipt(self.state, receipt, prior,
                                  is_present=lambda pid: False)
        self.assertEqual(archived.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(receipt.exists())

    def test_recovery_rejects_receipt_change(self):
        from quickstart.a4.observer_runtime import _archive_dead_receipt
        receipt = self.state / "a4-observer-launch.json"
        prior = {"pid": 43123, "port": 18766, "profile": "observer"}
        receipt.write_text(json.dumps({"pid": 43124,
                                       "port": 18766,
                                       "profile": "observer"}), encoding="utf-8")
        with self.assertRaisesRegex(ObserverStartupError, "receipt_changed"):
            _archive_dead_receipt(self.state, receipt, prior,
                                  is_present=lambda pid: False)

    def test_occupied_owned_and_authenticated_observer_is_reused(self):
        from unittest.mock import patch
        cfg=self.state / "a4-observer-config.json"
        receipt=self.state / "a4-observer-launch.json"
        receipt.write_text(json.dumps({"pid":33001, "port":18766,
                                       "profile":"observer"}), encoding="utf-8")
        native=self.state / "native"
        (native / "workspace_mcp").mkdir(parents=True)
        (native / "manage.py").write_text("# host", encoding="utf-8")
        (native / "workspace_mcp" / "http_server.py").write_text("# http",encoding="utf-8")
        with patch("quickstart.a4.observer_runtime.local_port_free",return_value=False), \
             patch("quickstart.a4.observer_runtime._owned_observer_process",return_value=True), \
             patch("quickstart.a4.observer_runtime.audit_readonly",
                   return_value={"ok":True,"read_only":True,"tool_count":6}):
            result=start(project=self.project,settings_path=self.settings,
                         state=self.state,token=self.token,port=18766,host=native)
        self.assertTrue(result["reused"])
        self.assertEqual(result["pid"],33001)

    @unittest.skipUnless(os.name == "nt", "Live host local Windows smoke")
    def test_actual_native_http_readonly_start_and_negative_call(self):
        try:
            host = locate_native_host(PROJECT)
        except ObserverStartupError:
            self.skipTest("Native MCP host mirror unavailable")
        port = self.free_port()
        result = start(project=self.project, settings_path=self.settings,
                       state=self.state, token=self.token, port=port,
                       host=host)
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_count"],6)
        pid=result["pid"]
        try:
            from quickstart.a4.observer_runtime import _call
            response = _call(port,self.token,{"jsonrpc":"2.0", "id":7,
                      "method":"tools/call","params":{
                         "name":"write_file","arguments":{
                             "workspace":"hermes","path":"should-not-exist.txt",
                             "content":"not allowed"}}})
            self.assertIn("error",response)
            self.assertFalse((self.project / "should-not-exist.txt").exists())
        finally:
            # Only stop the test-owned process whose PID start() returned.
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
            from quickstart.a4.observer_runtime import _LAUNCHED_CHILDREN
            owned=_LAUNCHED_CHILDREN.pop(pid, None)
            if owned is not None:
                owned.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
