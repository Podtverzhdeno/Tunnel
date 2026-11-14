"""Yandex read-only handoff diagnostics: synthetic browser fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from quickstart.a4.yandex_handoff_readiness import (
    observe_profiles, evaluate, bridge_probe, URL,
)


class YandexHandoffReadinessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "User Data"
        self.root.mkdir()
        self.default = self.root / "Default"
        self.default.mkdir()

    def register(self, *, manifest=None, other=None):
        if manifest is None:
            manifest = {"name": "Hermes AutoPilot", "version": "0.8.1"}
        data = {"extensions": {"settings": {
            "fake-extension-id": {"manifest": manifest,
                                  "path": "extensions/autopilot"},
            "unrelated-id": {"manifest": {
                "name": "Password Manager", "version": "23.3.1",
                "account": "private metadata not for output"},
                "path": "extensions/unrelated"}
        }}}
        if other:
            data["private_account"] = other
        (self.default / "Secure Preferences").write_text(
            json.dumps(data), encoding="utf-8")

    def test_empty_tabs_diagnosed_when_content_script_missing(self):
        self.register(other={"id": "personally identifying data"})
        result = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED")
        self.assertEqual(result["status"], "NO_CONTENT_SCRIPT_HANDOFF")
        self.assertTrue(result["autopilot_registered"])
        self.assertEqual(result["registered_versions"], ["0.8.1"])
        self.assertEqual(result["web_companion_bridge"], "AUTH_REQUIRED")
        self.assertFalse(result["prompt_dispatched"])
        self.assertFalse(result["dev_launch_blocked"])
        self.assertNotIn("personally identifying data", str(result))
        self.assertNotIn("Password Manager", str(result))
        self.assertNotIn("fake-extension-id", str(result))

    def test_safe_demo_explicit_marker_overrides_script_presence(self):
        self.register(manifest={
            "name": "Hermes AutoPilot", "version": "0.8.5",
            "description": "SAFE_DEMO",
            "content_scripts": [{"matches": ["https://chatgpt.com/*"],
                                 "js": ["assignment_panel.js"]}],
        })
        result = evaluate(user_data=self.root, bridge=lambda: "HTTP_OK_UNAUTHENTICATED_NOT_E2E")
        self.assertEqual(result["status"], "SAFE_DEMO_NO_AUTOMATIC_SEND")
        self.assertTrue(result["declared_content_scripts"])
        self.assertFalse(result["telegram_delivery_verified"])

    def test_non_demo_declared_script_is_not_delivery_proof(self):
        self.register(manifest={
            "name": "Hermes AutoPilot", "version": "1.0.0",
            "content_scripts": [{"js": ["content.js"]}],
        })
        result = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED")
        self.assertEqual(result["status"], "HANDOFF_NOT_LIVE_VERIFIED")
        self.assertFalse(result["chatgpt_response_verified"])
        self.assertFalse(result["telegram_delivery_verified"])

    def test_no_registered_autopilot(self):
        result = evaluate(user_data=self.root, bridge=lambda: "UNREACHABLE")
        self.assertEqual(result["status"], "AUTOPILOT_NOT_REGISTERED")
        self.assertFalse(result["dev_launch_blocked"])

    def test_invalid_profile_and_symlink_do_not_escape_root(self):
        bad = self.root / "Profile 21extra"
        bad.mkdir()
        (bad / "Secure Preferences").write_text("bad", encoding="utf8")
        path = self.default / "Secure Preferences"
        try:
            path.symlink_to(bad / "Secure Preferences")
        except (OSError, NotImplementedError):
            self.skipTest("Windows symlink creation not available")
        self.assertEqual(observe_profiles(self.root)["autopilot_registered"], 0)

    def test_oversized_or_invalid_registry_is_not_trusted(self):
        (self.default / "Secure Preferences").write_text("{oops", encoding="utf8")
        self.assertEqual(observe_profiles(self.root)["autopilot_registered"], 0)

    def test_unauthorized_bridge_is_distinct_from_unreachable(self):
        error = urllib.error.HTTPError(URL, 403, "Forbidden", None, None)
        class DummyOpener:
            def open(self, *_a, **_kw):
                raise error
        with patch("quickstart.a4.yandex_handoff_readiness.urllib.request.build_opener",
                   return_value=DummyOpener()):
            self.assertEqual(bridge_probe(), "AUTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
