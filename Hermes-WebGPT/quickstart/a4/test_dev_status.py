"""Deterministic DEV health checks without connecting to live services."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from quickstart.a4.dev_status import ENDPOINTS, status


class DevStatusTests(unittest.TestCase):
    @staticmethod
    def checked(*args, **kwargs):
        return status(*args,
                      capacity_probe=lambda: {"local_relay_authenticated": False},
                      **kwargs)

    @staticmethod
    def healthy(url):
        if url.endswith("/healthz"):
            return {"status": "ok", "tools": 28}
        if url.endswith("/readyz"):
            return "ready"
        if url.endswith("/health/control-plane"):
            return {"state": "polling", "status": "ok"}
        if url.endswith("/health"):
            return {"service": "hermes-webgpt-relay", "protocol": 1}
        raise AssertionError("unexpected endpoint")

    def test_direct_file_entrypoint_imports_without_pythonpath(self):
        # The Windows wizard and Local Workspace invoke the status script
        # by absolute path. A module-only unit test used to miss this bug.
        script=Path(__file__).resolve().parent/"dev_status.py"
        with tempfile.TemporaryDirectory(prefix="hermes-dev-status-cwd-") as tmp:
            env=os.environ.copy()
            env.pop("PYTHONPATH",None)
            result=subprocess.run(
                [sys.executable,str(script),"--help"],
                cwd=tmp,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("--json",result.stdout)

    def test_ready_four_still_not_cloud_e2e(self):
        r=self.checked(fetch=self.healthy,
                 gateway_plan=lambda: {"action": "already_running", "pid": 123})
        self.assertTrue(r["local_ready"])
        self.assertTrue(r["dev_mode_can_use_existing_services"])
        self.assertEqual(r["tunnel_control_state"], "polling")
        self.assertFalse(r["telegram_roundtrip_verified"])
        self.assertFalse(r["browser_chatgpt_delivery_verified"])
        self.assertFalse(r["dev_permission_boundary_enforced"])
        self.assertEqual(r["mcp_tools_observed"], 28)

    def test_three_authenticated_relay_slots_not_automatic_browser_e2e(self):
        outcome = status(
            fetch=self.healthy,
            gateway_plan=lambda: {"action": "already_running"},
            capacity_probe=lambda: {
                "local_relay_authenticated": True,
                "running_relay_slots": 3,
            },
        )
        self.assertEqual(outcome["relay_running_slots_authenticated"], 3)
        self.assertTrue(outcome["relay_three_slot_capacity_verified"])
        self.assertFalse(outcome["three_parallel_browser_requests_e2e_verified"])
        self.assertFalse(outcome["browser_chatgpt_delivery_verified"])

    def test_absent_gateway_does_not_fake_local_ready(self):
        r=self.checked(fetch=self.healthy,
                 gateway_plan=lambda: {"action": "start"})
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["gateway_process"])

    def test_health_exception_fails_closed_without_secrets(self):
        def broken(url):
            if url.endswith("/readyz"):
                raise TimeoutError()
            return self.healthy(url)
        r=self.checked(fetch=broken,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["tunnel_local"])

    def test_control_plane_network_problem_reported_separately(self):
        def fetch(url):
            if url.endswith("/health/control-plane"):
                return {"state": "backoff", "reason_code": "network_error",
                        "message": "private-error-do-not-display"}
            return self.healthy(url)
        r=self.checked(fetch=fetch,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertEqual(r["tunnel_control_reason"], "network_error")
        self.assertEqual(r["tunnel_control_state"], "backoff")
        self.assertNotIn("private-error", str(r))
        self.assertTrue(r["local_ready"])

    def test_unknown_gateway_raises_block(self):
        from quickstart.services import StartupBlocked
        def gw():
            raise StartupBlocked("gateway_not_attested")
        r=self.checked(fetch=self.healthy, gateway_plan=gw)
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["gateway_process"])

    def test_mcp_zero_tools_denied(self):
        def broken(url):
            if url.endswith("/healthz"):
                return {"status": "ok", "tools": 0}
            return self.healthy(url)
        r=self.checked(fetch=broken,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertFalse(r["local_ready"])

if __name__ == "__main__":
    unittest.main()
