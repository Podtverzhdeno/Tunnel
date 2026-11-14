"""A4 extension intake regression tests: no real browser or profile changes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from quickstart.a4.extension_contract import ExtensionError,inspect_extension


class ExtensionIntakeTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.manifest={
            "manifest_version":3,
            "name":"Hermes AutoPilot",
            "version":"0.9.0",
            "permissions":["tabs","alarms","storage"],
            "host_permissions":["https://chatgpt.com/*","http://127.0.0.1:18765/*"],
            "background":{"service_worker":"background.js","type":"module"},
            "action":{"default_popup":"popup.html"},
            "content_scripts":[{"matches":["https://chatgpt.com/*"],
                               "js":["content.js"]}],
        }
        for name in ("background.js","content.js"):
            (self.root/name).write_text("console.log('fixture')",encoding="utf8")
        (self.root/"popup.html").write_text("safe fixture",encoding="utf8")

    def write_manifest(self)->str:
        raw=json.dumps(self.manifest).encode("utf8")
        (self.root/"manifest.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_approved_candidate_is_not_install_or_delivery_proof(self):
        sha=self.write_manifest()
        result=inspect_extension(self.root,approved_manifest_sha256=sha)
        self.assertTrue(result["manifest_hash_matches_expected"])
        self.assertTrue(result["eligible_for_independent_review"])
        self.assertFalse(result["production_candidate"])
        self.assertTrue(result["independent_a5_review_required"])
        self.assertFalse(result["installed_in_yandex"])
        self.assertFalse(result["chatgpt_message_delivery_verified"])

    def test_demo_marker_downgrades_even_with_approved_hash(self):
        self.manifest["description"]="SAFE_DEMO: Messages are not automatically sent."
        sha=self.write_manifest()
        result=inspect_extension(self.root,approved_manifest_sha256=sha)
        self.assertTrue(result["demo_only"])
        self.assertFalse(result["eligible_for_independent_review"])
        self.assertFalse(result["production_candidate"])

    def test_wrong_or_missing_hash_is_not_approved(self):
        self.write_manifest()
        self.assertFalse(inspect_extension(
            self.root)["manifest_hash_matches_expected"])
        self.assertFalse(inspect_extension(
            self.root,approved_manifest_sha256="0"*64)["eligible_for_independent_review"])

    def test_extra_host_permissions_or_all_urls_denied(self):
        for hosts in (["https://chatgpt.com/*","<all_urls>"],
                      ["https://chatgpt.com/*","https://evil.example/*"]):
            self.manifest["host_permissions"]=hosts
            self.write_manifest()
            with self.assertRaisesRegex(ExtensionError,"permissions"):
                inspect_extension(self.root)

    def test_unsafe_extension_privileges_denied(self):
        self.manifest["permissions"].append("debugger")
        self.write_manifest()
        with self.assertRaisesRegex(ExtensionError,"permissions"):
            inspect_extension(self.root)

    def test_missing_worker_or_content_script_denied(self):
        self.write_manifest()
        (self.root/"background.js").unlink()
        with self.assertRaisesRegex(ExtensionError,"missing"):
            inspect_extension(self.root)

    def test_external_update_and_connect_denied(self):
        for field,val in (("update_url","https://evil.example/a.xml"),
                          ("externally_connectable",{"matches":["<all_urls>"]})):
            self.manifest[field]=val
            self.write_manifest()
            with self.assertRaisesRegex(ExtensionError,"external"):
                inspect_extension(self.root)
            del self.manifest[field]

    def test_path_traversal_denied(self):
        self.manifest["background"]["service_worker"]="../outside.js"
        self.write_manifest()
        with self.assertRaisesRegex(ExtensionError,"unsafe_extension_asset_path"):
            inspect_extension(self.root)

    def test_unsupported_mv2_denied(self):
        self.manifest["manifest_version"]=2
        self.write_manifest()
        with self.assertRaisesRegex(ExtensionError,"unsupported"):
            inspect_extension(self.root)

    def test_symlink_asset_denied_when_supported(self):
        (self.root/"background.js").unlink()
        try:
            (self.root/"background.js").symlink_to(
                self.root/"content.js")
        except (OSError,NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        self.write_manifest()
        with self.assertRaisesRegex(ExtensionError,"missing_or_outside"):
            inspect_extension(self.root)


if __name__=="__main__":
    unittest.main()
