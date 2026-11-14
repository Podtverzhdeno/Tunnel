"""Deterministic DEV health checks without connecting to live services."""
import unittest

from quickstart.a4.dev_status import ENDPOINTS, status


class DevStatusTests(unittest.TestCase):
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

    def test_ready_four_still_not_cloud_e2e(self):
        r=status(fetch=self.healthy,
                 gateway_plan=lambda: {"action": "already_running", "pid": 123})
        self.assertTrue(r["local_ready"])
        self.assertTrue(r["dev_mode_can_use_existing_services"])
        self.assertEqual(r["tunnel_control_state"], "polling")
        self.assertFalse(r["telegram_roundtrip_verified"])
        self.assertFalse(r["browser_chatgpt_delivery_verified"])
        self.assertFalse(r["dev_permission_boundary_enforced"])
        self.assertEqual(r["mcp_tools_observed"], 28)

    def test_absent_gateway_does_not_fake_local_ready(self):
        r=status(fetch=self.healthy,
                 gateway_plan=lambda: {"action": "start"})
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["gateway_process"])

    def test_health_exception_fails_closed_without_secrets(self):
        def broken(url):
            if url.endswith("/readyz"):
                raise TimeoutError()
            return self.healthy(url)
        r=status(fetch=broken,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["tunnel_local"])

    def test_control_plane_network_problem_reported_separately(self):
        def fetch(url):
            if url.endswith("/health/control-plane"):
                return {"state": "backoff", "reason_code": "network_error",
                        "message": "private-error-do-not-display"}
            return self.healthy(url)
        r=status(fetch=fetch,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertEqual(r["tunnel_control_reason"], "network_error")
        self.assertEqual(r["tunnel_control_state"], "backoff")
        self.assertNotIn("private-error", str(r))
        self.assertTrue(r["local_ready"])

    def test_unknown_gateway_raises_block(self):
        from quickstart.services import StartupBlocked
        def gw():
            raise StartupBlocked("gateway_not_attested")
        r=status(fetch=self.healthy, gateway_plan=gw)
        self.assertFalse(r["local_ready"])
        self.assertFalse(r["local_components"]["gateway_process"])

    def test_mcp_zero_tools_denied(self):
        def broken(url):
            if url.endswith("/healthz"):
                return {"status": "ok", "tools": 0}
            return self.healthy(url)
        r=status(fetch=broken,
                 gateway_plan=lambda: {"action": "already_running"})
        self.assertFalse(r["local_ready"])

if __name__ == "__main__":
    unittest.main()
