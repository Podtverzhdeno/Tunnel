"""Offline tests. No actual browser, credentials, APIs or Windows dependencies."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("extension_mirror_guard.py")
spec = importlib.util.spec_from_file_location("extension_mirror_guard", MODULE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
SHA = "a" * 40


def manifest():
    return {"manifest_version": 3, "version": "0.6.6", "name": "Test Extension", "description":"Synthetic",
            "background": {"service_worker": "background.js"},
            "action": {"default_popup": "popup.html", "default_title": "diagnostics"},
            "permissions": list(g.PERMS),
            "host_permissions": list(g.HOSTS),
            "content_scripts": [{"matches": ["https://chatgpt.com/*"],
                                 "js": list(g.SCRIPTS), "run_at": "document_idle"}]}


class MirrorGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / g.SOURCE
        self.mirror = self.root / g.COPY
        self.src.mkdir(parents=True)
        self.mirror.mkdir()
        for name in g.PAIR:
            data = (json.dumps(manifest()).encode() if name == "manifest.json"
                    else ("// synthetic public extension file " + name).encode())
            (self.src/name).write_bytes(data)
            (self.mirror/name).write_bytes(data)

    def test_all_seven_files_match(self):
        with patch.object(g, "_validate_revision", return_value=True) as validate:
            r = g.inspect(self.root, SHA)
        validate.assert_called_once_with(self.root.resolve(), SHA)
        self.assertEqual(r["status"], "source_pair_verified")
        self.assertEqual(r["files_verified"], 7)
        self.assertEqual(r["source_revision"], SHA)
        self.assertTrue(r["source_revision_verified"])

    def test_identity_digests_reproducible(self):
        a, b = g.inspect(self.root), g.inspect(self.root)
        self.assertEqual(a["evidence_sha256"], b["evidence_sha256"])

    def test_audit_never_claims_runtime(self):
        r = g.inspect(self.root)
        self.assertFalse(r["runtime_verified"])
        self.assertFalse(r["external_effects_verified"])
        self.assertFalse(r["no_replay_authority"])
        self.assertFalse(r["source_revision_verified"])

    def test_change_in_background_fails(self):
        (self.mirror/"background.js").write_text("different")
        with self.assertRaisesRegex(g.UnsafeContract, "MIRROR_CONTENT_DIVERGED:background.js"):
            g.inspect(self.root)

    def test_change_in_manifest_fails(self):
        m = manifest(); m["version"] = "0.7.0"
        (self.src/"manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(g.UnsafeContract, "MIRROR_CONTENT_DIVERGED:manifest.json"):
            g.inspect(self.root)

    def test_manifest_version_bump_requires_review(self):
        self.mutate_manifest("version", "0.7.0")
        with self.assertRaisesRegex(g.UnsafeContract, "EXTENSION_VERSION_REVIEW_REQUIRED"):
            g.inspect(self.root)

    def test_manifest_version_only_mv3(self):
        self.mutate_manifest("manifest_version", 2)
        with self.assertRaisesRegex(g.UnsafeContract, "MANIFEST_VERSION_UNEXPECTED"):
            g.inspect(self.root)

    def test_manifest_version_bool_not_int(self):
        self.mutate_manifest("manifest_version", True)
        with self.assertRaisesRegex(g.UnsafeContract, "MANIFEST_VERSION_UNEXPECTED"):
            g.inspect(self.root)

    def test_host_scope_broadening_rejected(self):
        self.mutate_manifest("host_permissions", ["<all_urls>"])
        with self.assertRaisesRegex(g.UnsafeContract, "HOST_SCOPE_CHANGED"):
            g.inspect(self.root)

    def test_permission_debugger_rejected(self):
        self.mutate_manifest("permissions", list(g.PERMS)+["debugger"])
        with self.assertRaisesRegex(g.UnsafeContract, "PERMISSION_SET_CHANGED"):
            g.inspect(self.root)

    def test_content_script_injection_scope_rejected(self):
        self.mutate_manifest("content_scripts", [{"matches":["<all_urls>"],
           "js":list(g.SCRIPTS),"run_at":"document_idle"}])
        with self.assertRaisesRegex(g.UnsafeContract, "CONTENT_SCRIPT_SCOPE_CHANGED"):
            g.inspect(self.root)

    def test_content_script_list_rejected(self):
        self.mutate_manifest("content_scripts", [{"matches":["https://chatgpt.com/*"],
           "js":["other.js"],"run_at":"document_idle"}])
        with self.assertRaisesRegex(g.UnsafeContract, "CONTENT_SCRIPT_SCOPE_CHANGED"):
            g.inspect(self.root)

    def test_background_worker_replacement_rejected(self):
        self.mutate_manifest("background", {"service_worker":"other.js"})
        with self.assertRaisesRegex(g.UnsafeContract, "BACKGROUND_WORKER_CHANGED"):
            g.inspect(self.root)

    def test_invalid_json_fails(self):
        (self.src/"manifest.json").write_text("NOT JSON")
        (self.mirror/"manifest.json").write_text("NOT JSON")
        with self.assertRaisesRegex(g.UnsafeContract, "MANIFEST_JSON_INVALID"):
            g.inspect(self.root)

    def test_manifest_not_object_fails(self):
        for parent in (self.src,self.mirror): (parent/"manifest.json").write_text("[]")
        with self.assertRaisesRegex(g.UnsafeContract, "MANIFEST_NOT_OBJECT"):
            g.inspect(self.root)

    def test_additional_manifest_permission_surface_rejected(self):
        self.mutate_manifest("optional_host_permissions", ["https://*/*"])
        with self.assertRaisesRegex(g.UnsafeContract, "MANIFEST_FIELDS_CHANGED"):
            g.inspect(self.root)

    def test_popup_redirect_rejected(self):
        self.mutate_manifest("action", {"default_popup":"remote.html", "default_title":"hi"})
        with self.assertRaisesRegex(g.UnsafeContract, "ACTION_POPUP_CHANGED"):
            g.inspect(self.root)

    @unittest.skipUnless(shutil.which("git"), "git missing")
    def test_verified_revision_binds_exact_checkout(self):
        self.git_init()
        sha = self.git_head()
        got = g.inspect(self.root, sha)
        self.assertTrue(got["source_revision_verified"])
        self.assertEqual(got["source_revision"], sha)

    @unittest.skipUnless(shutil.which("git"), "git missing")
    def test_fake_sha_rejected_even_if_format_valid(self):
        self.git_init()
        with self.assertRaisesRegex(g.EvidenceUnavailable, "SOURCE_REVISION_NOT_CHECKED_OUT"):
            g.inspect(self.root, SHA)

    @unittest.skipUnless(shutil.which("git"), "git missing")
    def test_dirty_both_mirrors_cannot_claim_committed_sha(self):
        self.git_init()
        sha = self.git_head()
        for base in (self.src,self.mirror):
            (base/"core.js").write_text("dirty-but-mirrored")
        with self.assertRaisesRegex(g.EvidenceUnavailable, "SOURCE_FILES_DIRTY_OR_UNKNOWN"):
            g.inspect(self.root, sha)

    def git_head(self):
        return subprocess.check_output(["git", "-C", str(self.root),
                                        "rev-parse", "HEAD"], text=True).strip()

    def git_init(self):
        for arg in (["init", "-q"], ["config", "user.name", "Test"],
                    ["config", "user.email", "test@example.invalid"],
                    ["add", "chat-launch-smoke-desktop-test"],
                    ["commit", "-qm", "synthetic"]):
            subprocess.run(["git", "-C", str(self.root), *arg], check=True,
                           capture_output=True)

    def test_missing_browser_file_unknown(self):
        (self.src/"popup.js").unlink()
        with self.assertRaisesRegex(g.EvidenceUnavailable, "EVIDENCE_FILE_MISSING"):
            g.inspect(self.root)

    def test_symlink_file_rejected(self):
        target = self.mirror/"background.js"
        target.unlink()
        try:
            target.symlink_to(self.src/"background.js")
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(g.EvidenceUnavailable, "EVIDENCE_SYMLINK"):
            g.inspect(self.root)

    def test_symlink_parent_rejected(self):
        (self.mirror/"background.js").unlink()
        for f in list(self.mirror.iterdir()): f.unlink()
        self.mirror.rmdir()
        try: self.mirror.symlink_to(self.src, target_is_directory=True)
        except OSError: self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(g.EvidenceUnavailable, "EVIDENCE_SYMLINK"):
            g.inspect(self.root)

    def test_oversized_file_rejected(self):
        (self.src/"core.js").write_bytes(b"x" * (g.MAX_FILE_BYTES+1))
        with self.assertRaisesRegex(g.EvidenceUnavailable, "EVIDENCE_FILE_TOO_LARGE"):
            g.inspect(self.root)

    def test_extra_private_file_is_not_read(self):
        for directory in (self.src,self.mirror):
            (directory/"bridge-config.json").write_text('! NOT VALID JSON secret=private-123 !')
        evidence = g.inspect(self.root)
        self.assertNotIn("bridge-config.json", json.dumps(evidence))
        self.assertNotIn("private-123", json.dumps(evidence))

    def test_extra_installable_javascript_rejected(self):
        (self.mirror / "unreviewed.js").write_text("/* injected */")
        with self.assertRaisesRegex(g.UnsafeContract,
                                    "INSTALLATION_PACKAGE_FILESET_CHANGED"):
            g.inspect(self.root)

    def test_extra_installable_directory_rejected(self):
        (self.mirror / "runtime-extra").mkdir()
        with self.assertRaisesRegex(g.UnsafeContract,
                                    "INSTALLATION_PACKAGE_FILESET_CHANGED"):
            g.inspect(self.root)

    def test_private_config_stays_unread_and_optional(self):
        path = self.mirror / "bridge-config.json"
        path.write_text("DO NOT READ private token")
        result = g.inspect(self.root)
        self.assertEqual(result["files_verified"], 7)
        self.assertNotIn("DO NOT READ", json.dumps(result))

    def test_private_config_symlink_rejected_without_opening(self):
        path = self.mirror / "bridge-config.json"
        try:
            path.symlink_to(self.root / "outside-secret")
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(g.UnsafeContract, "INSTALLATION_CONFIG_SYMLINK"):
            g.inspect(self.root)

    def test_no_path_leak_in_report(self):
        r = g.inspect(self.root)
        self.assertNotIn(str(self.root), json.dumps(r))

    def test_revision_rejected_on_unvalidated_input(self):
        with self.assertRaisesRegex(g.EvidenceUnavailable, "INVALID_SOURCE_REVISION"):
            g.inspect(self.root, "bad/secret/path")

    def test_incident_reason_sanitized(self):
        r = g._incident(g.UnsafeContract("MIRROR_CONTENT_DIVERGED:popup.js"), SHA)
        self.assertEqual(r["reason_code"], "MIRROR_CONTENT_DIVERGED")
        self.assertEqual(r["file"], "popup.js")
        self.assertFalse(r["runtime_verified"])

    def test_git_pathspec_has_platform_independent_separators(self):
        # Executing real Git HEAD binding on Windows in CI covers pathspec
        # normalization as well as source pairing, not just string equality.
        paths = [(folder / name).as_posix() for folder in (g.SOURCE, g.COPY)
                 for name in g.PAIR]
        self.assertTrue(all("\\\\" not in path for path in paths))
        self.assertEqual(len(paths),14)

    def test_sanitized_incident_unknown_not_healthy(self):
        r = g._incident(g.EvidenceUnavailable("EVIDENCE_READ_FAILED"), None)
        self.assertEqual(r["status"], "evidence_unknown")

    def test_cli_match_returns_zero_and_json(self):
        output = self.root/"mirror-report.json"
        rc = g.main(["--root",str(self.root),"--report",str(output)])
        self.assertEqual(rc,0)
        self.assertEqual(json.loads(output.read_text())["status"],"source_pair_verified")

    def test_cli_mismatch_returns_two_and_json(self):
        (self.mirror/"popup.js").write_text("mismatch")
        output = self.root/"mirror-report.json"
        rc = g.main(["--root",str(self.root),"--report",str(output)])
        self.assertEqual(rc,2)
        self.assertEqual(json.loads(output.read_text())["status"],"blocked")

    def test_cli_missing_returns_three_and_json(self):
        (self.src/"popup.js").unlink()
        output = self.root/"mirror-report.json"
        rc = g.main(["--root",str(self.root),"--report",str(output)])
        self.assertEqual(rc,3)
        self.assertEqual(json.loads(output.read_text())["status"],"evidence_unknown")

    def test_atomic_report_path_only(self):
        output=self.root/"nested"/"mirror-report.json"
        g._save_json(output,{"x":1})
        self.assertEqual(json.loads(output.read_text()),{"x":1})
        self.assertFalse(list(output.parent.glob(".mirror-report-*")))

    def test_report_never_overwrites_browser_code(self):
        target = self.src / "mirror-report.json"
        self.assertEqual(g.main(["--root",str(self.root),"--report",str(target)]),3)
        self.assertFalse(target.exists())

    def test_report_never_overwrites_private_config(self):
        target = self.mirror / "bridge-config.json"
        target.write_text("user-secret")
        self.assertEqual(g.main(["--root",str(self.root),"--report",str(target)]),3)
        self.assertEqual(target.read_text(), "user-secret")

    def test_report_path_traversal_rejected(self):
        with self.assertRaisesRegex(g.EvidenceUnavailable,"REPORT_PATH_TRAVERSAL"):
            g._safe_report_target(self.root,Path("../mirror-report.json"))

    def test_report_outside_checkout_rejected(self):
        with self.assertRaisesRegex(g.EvidenceUnavailable,"REPORT_OUTSIDE_CHECKOUT"):
            g._safe_report_target(self.root,self.root.parent/"mirror-report.json")

    def test_report_git_metadata_protected(self):
        with self.assertRaisesRegex(g.EvidenceUnavailable,"REPORT_PATH_PROTECTED"):
            g._safe_report_target(self.root,Path(".git/mirror-report.json"))

    def test_report_symlink_target_rejected(self):
        target=self.root/"mirror-report.json"
        try: target.symlink_to(self.root/"target-secret")
        except OSError: self.skipTest("Symlink unavailable")
        with self.assertRaisesRegex(g.EvidenceUnavailable,"REPORT_SYMLINK"):
            g._safe_report_target(self.root,target)

    def test_large_json_values_not_logged(self):
        evidence = g.inspect(self.root)
        self.assertTrue(all(set(x)=={"file","bytes","sha256"} for x in evidence["file_digests"]))

    def mutate_manifest(self, field, value):
        m=manifest();m[field]=value
        for base in (self.src,self.mirror):
            (base/"manifest.json").write_text(json.dumps(m))


if __name__ == "__main__":
    unittest.main()
