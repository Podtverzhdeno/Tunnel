"""Isolated loaded-vs-source Relay parity tests, no live service touches."""
import hashlib
import tempfile
from pathlib import Path
import unittest

from quickstart.a4.loaded_runtime_parity import compare_loaded, RUNTIME_FILES


class LoadedRuntimeParityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.hashes = {}
        for i, name in enumerate(RUNTIME_FILES):
            data = f"safe-synthetic-source-{i}".encode("utf8")
            (self.root / name).write_bytes(data)
            self.hashes[name] = hashlib.sha256(data).hexdigest()

    def fetch(self):
        return {
            "ok": True,
            "service": "hermes-webgpt-relay",
            "protocol": 1,
            "runtime_build": dict(self.hashes),
            "private": "PRIVATE_REQUEST_CONTENT_SHOULD_NOT_APPEAR",
        }

    def test_all_four_hashes_match_process(self):
        report = compare_loaded(root=self.root, fetch=self.fetch)
        self.assertEqual(report["status"], "MATCH")
        self.assertEqual(report["files_checked"], 4)
        self.assertTrue(report["loaded_equals_current_source"])
        self.assertFalse(report["chatgpt_e2e_verified"])
        self.assertFalse(report["process_restarted"])
        self.assertNotIn("PRIVATE_REQUEST", str(report))
        self.assertNotIn(self.hashes[RUNTIME_FILES[0]], str(report))

    def test_changed_source_is_not_silently_accepted(self):
        (self.root / "relay.py").write_text("changed-after-start", encoding="utf8")
        report = compare_loaded(root=self.root, fetch=self.fetch)
        self.assertEqual(report["status"], "DISK_CHANGED_AFTER_RELAY_START")
        self.assertEqual(report["changed_since_loaded"], ["relay.py"])
        self.assertFalse(report["loaded_equals_current_source"])

    def test_missing_runtime_identity_is_unknown(self):
        for broken in [
            {"ok": True, "service": "hermes-webgpt-relay", "protocol": 1},
            {"ok": True, "service": "hermes-webgpt-relay", "protocol": 1,
             "runtime_build": {"relay.py": "bad"}},
            {"ok": False, "service": "hermes-webgpt-relay", "protocol": 1},
        ]:
            with self.subTest(broken=broken):
                report = compare_loaded(root=self.root, fetch=lambda: broken)
                self.assertFalse(report["loaded_equals_current_source"])
                self.assertFalse(report["source_hashes_checked"])

    def test_supplied_health_is_used_without_second_request(self):
        def forbidden():
            raise AssertionError("must not refetch a health endpoint")
        report = compare_loaded(root=self.root,
                                observed=self.fetch(), fetch=forbidden)
        self.assertEqual(report["status"], "MATCH")

    def test_missing_source_never_claims_match(self):
        (self.root / "relay.py").unlink()
        report = compare_loaded(root=self.root, fetch=self.fetch)
        self.assertEqual(report["status"], "SOURCE_FILE_UNVERIFIED")
        self.assertFalse(report["loaded_equals_current_source"])

    def test_fetch_exception_does_not_leak_details(self):
        def broken():
            raise OSError("private browser account must stay private")
        report = compare_loaded(root=self.root, fetch=broken)
        self.assertEqual(report["status"], "HEALTH_OR_SOURCE_ERROR")
        self.assertNotIn("private browser account", str(report))

    def test_symlinked_source_refused(self):
        target = self.root / "real.py"
        target.write_text("x", encoding="utf8")
        file = self.root / "relay.py"
        file.unlink()
        try:
            file.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privileges unavailable")
        report = compare_loaded(root=self.root, fetch=self.fetch)
        self.assertEqual(report["status"], "SOURCE_FILE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
