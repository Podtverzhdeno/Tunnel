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

    def register(self, *, manifest=None, other=None, installed_path="extensions/autopilot"):
        if manifest is None:
            manifest = {"name": "Hermes AutoPilot", "version": "0.8.1"}
        data = {"extensions": {"settings": {
            "fake-extension-id": {"manifest": manifest,
                                  "path": installed_path},
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
        self.assertEqual(result["status"], "NO_DECLARED_CONTENT_SCRIPT")
        self.assertFalse(result["dynamic_content_script_injection_verified"])
        self.assertTrue(result["autopilot_registered"])
        self.assertEqual(result["registered_versions"], ["0.8.1"])
        self.assertEqual(result["web_companion_bridge"], "AUTH_REQUIRED")
        self.assertFalse(result["prompt_dispatched"])
        self.assertFalse(result["dev_launch_blocked"])
        self.assertNotIn("personally identifying data", str(result))
        self.assertNotIn("Password Manager", str(result))
        self.assertNotIn("fake-extension-id", str(result))

    def test_incomplete_registry_metadata_does_not_claim_script_missing(self):
        # Some Yandex profiles retain only a name and installation path.
        self.register(manifest={"name": "Hermes AutoPilot"})
        report = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED")
        self.assertEqual(report["status"], "REGISTRY_MANIFEST_METADATA_INCOMPLETE")
        self.assertFalse(report["manifest_metadata_complete"])
        self.assertFalse(report["dynamic_content_script_injection_verified"])
        self.assertFalse(report["prompt_dispatched"])

    def test_complete_manifest_with_empty_scripts_does_not_rule_out_dynamic(self):
        self.register(manifest={"name": "Hermes AutoPilot", "version": "0.8.1",
                                "content_scripts": []})
        report = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED")
        self.assertEqual(report["status"], "NO_DECLARED_CONTENT_SCRIPT")
        self.assertTrue(report["explicit_empty_content_scripts"])
        self.assertFalse(report["dynamic_content_script_injection_verified"])

    def test_source_manifest_resolves_incomplete_registry_metadata(self):
        source = self.root.parent / "autopilot-v0.8.1"
        source.mkdir()
        (source / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "name": "Hermes AutoPilot",
            "version": "0.8.1", "description": "SAFE_DEMO",
            "background": {"service_worker": "background.js"},
        }), encoding="utf8")
        self.register(manifest={"name": "Hermes AutoPilot"}, installed_path=str(source))
        report = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED",
                          trusted_source_roots=(self.root.parent,))
        self.assertEqual(report["status"], "SAFE_DEMO_NO_AUTOMATIC_SEND")
        self.assertEqual(report["source_versions_observed"], ["0.8.1"])
        self.assertEqual(report["registered_versions"], [])
        self.assertTrue(report["registered_source_manifest_observed"])
        self.assertFalse(report["source_binding_to_running_worker_verified"])
        self.assertFalse(report["prompt_dispatched"])

    def test_untrusted_source_path_is_not_read_or_treated_as_verified(self):
        source = self.root.parent / "outside-trust-root"
        source.mkdir()
        (source / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "name": "Hermes AutoPilot",
            "version": "9.9.9", "description": "SAFE_DEMO"
        }), encoding="utf8")
        self.register(manifest={"name": "Hermes AutoPilot"}, installed_path=str(source))
        report = evaluate(user_data=self.root, bridge=lambda: "AUTH_REQUIRED",
                          trusted_source_roots=(self.root,))
        self.assertEqual(report["status"], "REGISTRY_MANIFEST_METADATA_INCOMPLETE")
        self.assertEqual(report["source_versions_observed"], [])
        self.assertFalse(report["registered_source_manifest_observed"])

    def test_missing_browser_directory_reports_nonregistered(self):
        report = evaluate(user_data=self.root / "not-found", bridge=lambda: "UNREACHABLE")
        self.assertEqual(report["status"], "AUTOPILOT_NOT_REGISTERED")
        self.assertEqual(report["registered_versions"], [])
        self.assertFalse(report["declared_content_scripts"])

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
