"""P085 protected-service startup: strict config and OS SID before any pipe."""
from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import p085_reviewer_service as srv
from workspace_mcp.review_grants import GrantDenied
from workspace_mcp.review_grant_broker import BrokerPolicy

SERVER="S-1-5-21-1000-1000-1000-101"
REVIEWER="S-1-5-21-1000-1000-1000-102"
UNTRUSTED="S-1-5-21-1000-1000-1000-103"

class ServiceStartupTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.service=self.root/"protected-service"
        self.project=self.root/"untrusted-workspace"/"code-repo"
        self.project.mkdir(parents=True)
        self.private=self.service/"p085_private"
        self.private.mkdir(parents=True)
        trusted=self.service/"p085_trusted"
        trusted.mkdir(parents=True)
        canonical=Path(__file__).resolve().parents[2]/"review_merge.py"
        source=canonical.read_bytes()
        (trusted/"review_merge.py").write_bytes(source)
        self.journal_sha=hashlib.sha256(source).hexdigest()
        self.config={
            "version":1,
            "service_sid":SERVER,
            "reviewer_client_sid":REVIEWER,
            "untrusted_mcp_sid":UNTRUSTED,
            "reviewer_actor":"reviewer:authenticated",
            "workspace":"projects",
            "owner_principals":{f"developer-{i}":f"developer:principal-{i}" for i in range(1,5)},
            "project_root":str(self.project),
            "qa_runner_actor":"qa:isolated-supervisor",
            "qa_runner_environment_sha256":"a"*64,
            "trusted_journal_sha256":self.journal_sha,
        }
        self._write_config()
        (self.private/"review-key.bin").write_bytes(b"offline-test-key-never-for-production!!!")
        (self.private/"qa-verify-key.bin").write_bytes(b"qa-supervisor-test-only-secret-not-real!!!")
        self.patcher=patch.object(srv,"current_process_sid",return_value=SERVER)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _write_config(self):
        (self.private/"config.json").write_text(
            json.dumps(self.config),encoding="utf-8")

    def load(self):
        return srv.load_configuration(service_root=self.service)

    def test_valid_is_only_startup_not_merge_authorization(self):
        policy,root,db,key,ledger,qa_policy,qa_key,journal_pin=self.load()
        self.assertEqual(policy.service_sid,SERVER)
        self.assertEqual(root,self.project.resolve())
        self.assertEqual(len(key),len(b"offline-test-key-never-for-production!!!"))
        self.assertFalse(ledger.exists())
        self.assertFalse(db.exists())
        self.assertEqual(qa_policy.runner_actor,"qa:isolated-supervisor")
        self.assertNotEqual(key,qa_key)
        self.assertEqual(journal_pin,self.journal_sha)

    def test_v2_stage_separates_immutable_code_from_private_state(self):
        stage=self.root/"new-protected-staging"
        code=stage/"code"
        private=stage/"private_state"
        journal=code/"p085_trusted"
        journal.mkdir(parents=True)
        private.mkdir()
        (journal/"review_merge.py").write_bytes(
            (self.service/"p085_trusted"/"review_merge.py").read_bytes())
        for name in ("config.json","review-key.bin","qa-verify-key.bin"):
            (private/name).write_bytes((self.private/name).read_bytes())
        from workspace_mcp.owner_cutover_gate import service_private_root
        self.assertEqual(service_private_root(code),private)
        policy,project,db,key,ledger,qa_policy,qa_key,pin=(
            srv.load_configuration(service_root=code))
        self.assertEqual(policy.service_sid,SERVER)
        self.assertEqual(project,self.project.resolve())
        self.assertEqual(pin,self.journal_sha)
        self.assertEqual(ledger.parent,private)
        self.assertFalse(ledger.exists())

    def test_v2_stage_never_falls_back_to_legacy_private_path(self):
        stage=self.root/"new-protected-staging"
        code=stage/"code"
        code.mkdir(parents=True)
        (code/"p085_private").mkdir()
        (code/"p085_private"/"config.json").write_text(
            json.dumps(self.config),encoding="utf-8")
        from workspace_mcp.owner_cutover_gate import service_private_root
        self.assertEqual(service_private_root(code),stage/"private_state")
        with self.assertRaisesRegex(GrantDenied,"PRIVATE_RESOURCE_MISSING"):
            srv.load_configuration(service_root=code)

    def test_mismatched_os_account_fails_before_service(self):
        with patch.object(srv,"current_process_sid",return_value=UNTRUSTED):
            with self.assertRaisesRegex(GrantDenied,"BROKER_PROCESS_IDENTITY_MISMATCH"):
                self.load()

    def test_service_location_inside_untrusted_project_denied(self):
        self.config["project_root"]=str(self.root)
        self._write_config()
        with self.assertRaisesRegex(GrantDenied,"PROTECTED_INSTALL_MUST_BE_OUTSIDE_PROJECT"):
            self.load()

    def test_sibling_inside_broad_mcp_workspace_denied(self):
        sibling = self.project.parent / "separate-service-but-still-shared"
        private = sibling / "p085_private"
        private.mkdir(parents=True)
        (private / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        (private / "review-key.bin").write_bytes(b"offline-test-key-never-for-production!!!")
        (private / "qa-verify-key.bin").write_bytes(b"qa-supervisor-test-only-secret-not-real!!!")
        with self.assertRaisesRegex(GrantDenied,"PROTECTED_INSTALL_MUST_BE_OUTSIDE_PROJECT"):
            srv.load_configuration(service_root=sibling)

    def test_missing_secret_denied(self):
        (self.private/"review-key.bin").unlink()
        with self.assertRaisesRegex(GrantDenied,"PRIVATE_RESOURCE_MISSING"):
            self.load()

    def test_missing_qa_key_denied(self):
        (self.private/"qa-verify-key.bin").unlink()
        with self.assertRaisesRegex(GrantDenied,"PRIVATE_RESOURCE_MISSING"):
            self.load()

    def test_same_grant_and_qa_key_denied(self):
        secret=(self.private/"review-key.bin").read_bytes()
        (self.private/"qa-verify-key.bin").write_bytes(secret)
        with self.assertRaisesRegex(GrantDenied,"PRIVATE_KEY_INVALID"):
            self.load()

    def test_untrusted_qa_runner_config_denied(self):
        self.config["qa_runner_environment_sha256"]="not-a-hash"
        self._write_config()
        with self.assertRaisesRegex(GrantDenied,"QA_RUNNER_POLICY_INVALID"):
            self.load()

    def test_changed_trusted_journal_refused(self):
        path=self.service/"p085_trusted"/"review_merge.py"
        path.write_text("class Bogus: pass",encoding="utf-8")
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_PIN_MISMATCH"):
            self.load()

    def test_missing_trusted_journal_refused(self):
        (self.service/"p085_trusted"/"review_merge.py").unlink()
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_MISSING"):
            self.load()

    def test_bad_schema_and_boolean_version_denied(self):
        for bad in (True,2,"1"):
            with self.subTest(bad=bad):
                self.config["version"]=bad
                self._write_config()
                with self.assertRaisesRegex(GrantDenied,"PRIVATE_CONFIG_INVALID"):
                    self.load()

    def test_root_same_as_reviewer_or_worker_principal_denied(self):
        for key in ("reviewer_client_sid","untrusted_mcp_sid"):
            with self.subTest(key=key):
                self.config[key]=SERVER
                self._write_config()
                with self.assertRaisesRegex(GrantDenied,"OS_PRINCIPALS_NOT_SEPARATED"):
                    self.load()
                self.config[key]=REVIEWER if key=="reviewer_client_sid" else UNTRUSTED

    def test_secret_symlink_denied_if_supported(self):
        target=self.private/"actual.bin"
        target.write_bytes(b"offline-test-key-never-for-production!!!")
        (self.private/"review-key.bin").unlink()
        try:
            (self.private/"review-key.bin").symlink_to(target)
        except (OSError,NotImplementedError):
            self.skipTest("Windows symlink unavailable")
        with self.assertRaisesRegex(GrantDenied,"PRIVATE_LINKED_PATH"):
            self.load()

    def test_invalid_config_traversal_not_executed(self):
        self.config["project_root"]="../code-repo"
        self._write_config()
        with self.assertRaisesRegex(GrantDenied,"PROJECT_ROOT_INVALID"):
            self.load()

    def test_no_ledger_materialized_by_doctor_load(self):
        self.load()
        self.load()
        self.assertEqual({f.name for f in self.private.iterdir()},
                         {"config.json","review-key.bin","qa-verify-key.bin"})

if __name__=="__main__":
    unittest.main()
