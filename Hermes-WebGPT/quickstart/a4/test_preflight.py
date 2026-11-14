"""A4 preflight unit tests: fake loopback, no browser/host changes."""
import tempfile
import unittest
from pathlib import Path

from quickstart.a4.preflight import PreflightError, inspect_services, preflight, validate_settings


class PreflightTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        self.project = root / "Hermes-WebGPT"
        self.project.mkdir()
        (root / "YandexBrowser" / "Application").mkdir(parents=True)
        self.browser = root / "YandexBrowser" / "Application" / "browser.exe"
        self.browser.write_bytes(b"test")
        self.cfg = {
            "schema_version": 1, "mode": "read_only", "workers": 3,
            "scope": str(self.project), "browser": "yandex",
            "browser_exe": str(self.browser),
            "enforcement": "pending_backend_integration",
        }

    def test_positive_validation_never_proves_permission_enforcement(self):
        data = validate_settings(self.cfg, project=self.project)
        self.assertEqual(data["enforcement"], "unverified")

    def test_invalid_modes_and_worker_counts(self):
        for mode in ("admin", "", 0, None):
            item = dict(self.cfg, mode=mode)
            with self.assertRaises(PreflightError):
                validate_settings(item, project=self.project)
        for count in (-1, 0, 6, True, 2.5):
            with self.assertRaises(PreflightError):
                validate_settings(dict(self.cfg, workers=count), project=self.project)

    def test_scope_outside_project_workspace_denied(self):
        new = dict(self.cfg, scope=str(self.project.parent.parent))
        with self.assertRaises(PreflightError):
            validate_settings(new, project=self.project)

    def test_executable_must_be_yandex_path(self):
        other = self.project / "browser.exe"
        other.write_bytes(b"untrusted")
        with self.assertRaises(PreflightError):
            validate_settings(dict(self.cfg, browser_exe=str(other)), project=self.project)

    def test_health_does_not_require_network(self):
        def fetch(url):
            if url.endswith("/healthz"):
                return {"status": "ok", "tools": 28}
            if url.endswith("/readyz"):
                return "ready"
            if url.endswith("/health"):
                return {"service": "hermes-webgpt-relay", "protocol": 1}
            raise AssertionError("unexpected URL")
        state = inspect_services(fetch=fetch)
        self.assertEqual(state, {"mcp": True, "tunnel": True, "relay": True})
        result = preflight(self.cfg, project=self.project, fetch=fetch)
        self.assertEqual(result["services"], state)
        self.assertFalse(result["ready_for_live_start"])
        self.assertIn("mcp_authorization_boundary_unverified", result["blockers"])

    def test_unhealthy_services_are_explicit(self):
        self.assertEqual(inspect_services(fetch=lambda _: (_ for _ in ()).throw(TimeoutError())),
                         {"mcp": False, "tunnel": False, "relay": False})


if __name__ == "__main__":
    unittest.main()
