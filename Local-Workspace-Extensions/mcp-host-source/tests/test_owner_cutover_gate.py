"""P085 owner cutover gates: offline only; no actual Windows ACL mutation."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workspace_mcp.review_grants import GrantDenied
from workspace_mcp.review_grant_broker import BrokerPolicy
from workspace_mcp.review_qa_attestation import QATrustPolicy
from workspace_mcp.owner_cutover_gate import (
    SCHEMA, CHECKS, require_owner_cutover,
)

NOW=datetime(2026,11,2,18,0,tzinfo=timezone.utc)
POLICY=BrokerPolicy(
    "S-1-5-21-1000-1000-1000-101",
    "S-1-5-21-1000-1000-1000-102",
    "S-1-5-21-1000-1000-1000-103",
    "reviewer:trusted","projects",
    {f"developer-{i}":f"developer:principal-{i}" for i in range(1,5)})
QA=QATrustPolicy("qa:isolated","f"*64)
JOURNAL="b"*64


class OwnerCutoverTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.service=self.root/"protected-service"
        self.private=self.service/"p085_private"
        self.private.mkdir(parents=True)
        self.project=self.root/"live-project"
        self.project.mkdir()
        self.path=self.private/"owner-cutover.json"
        self.data={
            "schema":SCHEMA,
            "service_sid":POLICY.service_sid,
            "reviewer_client_sid":POLICY.reviewer_client_sid,
            "untrusted_mcp_sid":POLICY.untrusted_mcp_sid,
            "project_root":str(self.project.resolve()),
            "trusted_journal_sha256":JOURNAL,
            "qa_runner_environment_sha256":QA.runner_environment_sha256,
            "recorded_at_utc":(NOW-timedelta(minutes=1)).isoformat(),
            "operator_checks":{k:True for k in CHECKS},
        }

    def verify(self):
        return require_owner_cutover(self.service,policy=POLICY,
                                     project=self.project,
                                     journal_sha256=JOURNAL,
                                     qa_policy=QA,now=NOW)
    def write(self):
        self.path.write_text(json.dumps(self.data),encoding="utf-8")

    def test_absent_owner_ack_never_enables(self):
        with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CUTOVER_NOT_ATTESTED"):
            self.verify()

    def test_exact_operator_ack_is_only_an_assertion(self):
        self.write()
        r=self.verify()
        self.assertTrue(r["owner_checks_asserted"])
        self.assertTrue(r["operator_verification_is_not_host_attestation"])

    def test_any_false_check_fails_closed(self):
        for name in sorted(CHECKS):
            with self.subTest(name=name):
                self.data["operator_checks"][name]=False
                self.write()
                with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CHECKS_INCOMPLETE"):
                    self.verify()
                self.data["operator_checks"][name]=True

    def test_extra_check_fails_closed(self):
        self.data["operator_checks"]["can_override_all"]=True
        self.write()
        with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CHECKS_INCOMPLETE"):
            self.verify()

    def test_scope_drift_fails_closed(self):
        for key,val in [
            ("trusted_journal_sha256","e"*64),
            ("qa_runner_environment_sha256","e"*64),
            ("service_sid","S-1-5-1234"),
            ("project_root","C:/other")
        ]:
            with self.subTest(key=key):
                good=self.data[key]
                self.data[key]=val
                self.write()
                with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CUTOVER_SCOPE_MISMATCH"):
                    self.verify()
                self.data[key]=good

    def test_stale_and_future_attestation_refused(self):
        for stamp in (NOW-timedelta(hours=25),NOW+timedelta(minutes=1)):
            self.data["recorded_at_utc"]=stamp.isoformat()
            self.write()
            with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CUTOVER_STALE_OR_INVALID"):
                self.verify()

    def test_boolean_string_not_valid(self):
        self.data["operator_checks"][next(iter(CHECKS))]="true"
        self.write()
        with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CHECKS_INCOMPLETE"):
            self.verify()

    def test_symlink_ack_refused(self):
        target=self.private/"untrusted.json"
        target.write_text("{}",encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError,NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(GrantDenied,"P085_OWNER_CUTOVER_NOT_ATTESTED"):
            self.verify()

if __name__=="__main__":
    unittest.main()
