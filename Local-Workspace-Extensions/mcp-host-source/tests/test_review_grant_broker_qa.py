"""P085 signed independent QA + trusted reviewer-grant offline integration."""
from __future__ import annotations

import hashlib
import hmac
import time
import unittest

from test_review_grant_broker import BrokerTests, ACTOR, REVIEWER, UNTRUSTED
from workspace_mcp.review_grants import GrantDenied
from workspace_mcp.review_qa_attestation import (
    AUDIENCE, ISSUER, REQUIRED, SCHEMA, QATrustPolicy, canonical,
)
from workspace_mcp.review_target_snapshot import snapshot

QAKEY=b"p085-offline-qa-proof-key-not-production!!"
POLICY=QATrustPolicy("qa:isolated-supervisor","f"*64)


class ProtectedQaBrokerTests(unittest.TestCase):
    def setUp(self):
        self.f=BrokerTests("test_scoped_reservation_is_not_review_accept")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def proof(self, *, signed=True, mutate=None):
        t=snapshot(self.f.root,self.f.db,"P152",workspace="projects",
                   owner_principals=self.f.policy.owner_principals)
        now=int(time.time())
        p={"schema":SCHEMA,"issuer":ISSUER,"audience":AUDIENCE,
           "task_id":t.task_id,"workspace":t.workspace,
           "revision":t.revision,"spec_sha256":t.spec_sha256,
           "source_bundle_sha256":t.source_bundle_sha256,
           "review_lease_sha256":t.review_lease_sha256,
           "runner_actor":POLICY.runner_actor,
           "runner_environment_sha256":POLICY.runner_environment_sha256,
           "issued_at":now-5,"expires_at":now+80,
           "checks":[{"id":n,"exit_code":0,
                      "evidence_sha256":format(i+1,"064x")}
                     for i,n in enumerate(REQUIRED)]}
        if mutate:
            mutate(p)
        return {"payload":p,
                "mac":hmac.new(QAKEY,canonical(p),hashlib.sha256).hexdigest()}

    def request(self, **kwargs):
        return {"task_id":"P152","signed_grant":self.f.env(),
                "signed_qa_receipt":self.proof(),**kwargs}

    def reserve(self, req=None):
        return self.f.broker.reserve_with_independent_qa(
            self.request() if req is None else req,
            connected_pipe_handle=19, qa_key=QAKEY,qa_policy=POLICY)

    def test_joint_os_grant_qa_is_reservation_only(self):
        v=self.reserve()
        self.assertEqual(v["state"],"RESERVED_REQUIRES_PRIVILEGED_QA")
        self.assertFalse(v["formal_review_performed"])
        self.assertFalse(v["source_merged"])
        self.assertEqual(set(v["qa_check_ids"]),set(REQUIRED))
        self.assertEqual(len(v["qa_receipt_sha256"]),64)

    def test_raw_role_and_missing_qa_cannot_claim(self):
        p=self.request()
        for forged in ({k:v for k,v in p.items() if k!="signed_qa_receipt"},
                       {**p,"actor_id":ACTOR},
                       {**p,"reviewer_sid":REVIEWER}):
            with self.subTest(fields=list(forged)),self.assertRaisesRegex(
                GrantDenied,"INVALID_QA_BROKER_REQUEST"):
                self.reserve(forged)
        self.assertEqual(self.f.ledger.state("a"*32),"UNKNOWN")

    def test_tampered_qa_receipt_cannot_claim(self):
        p=self.request()
        p["signed_qa_receipt"]["payload"]["checks"][0]["exit_code"]=7
        with self.assertRaisesRegex(GrantDenied,"QA_SIGNATURE_INVALID"):
            self.reserve(p)
        self.assertEqual(self.f.ledger.state("a"*32),"UNKNOWN")

    def test_qa_all_checks_required(self):
        p=self.request(signed_qa_receipt=self.proof(
            mutate=lambda x:x["checks"].pop()))
        with self.assertRaisesRegex(GrantDenied,"QA_CHECK_SET_INCOMPLETE"):
            self.reserve(p)

    def test_source_test_drift_denied_before_grant(self):
        p=self.request()
        (self.f.root/self.f.sandbox/self.f.test_file).write_bytes(
            b"def test_foo(): assert False\n")
        with self.assertRaisesRegex(GrantDenied,"QA_SOURCE_OR_LEASE_DRIFT"):
            self.reserve(p)
        self.assertEqual(self.f.ledger.state("a"*32),"UNKNOWN")

    def test_foreign_qa_runner_denied(self):
        bad=QATrustPolicy("qa:untrusted","f"*64)
        with self.assertRaisesRegex(GrantDenied,"QA_RUNNER_NOT_TRUSTED"):
            self.f.broker.reserve_with_independent_qa(
                self.request(),connected_pipe_handle=19,
                qa_key=QAKEY,qa_policy=bad)

    def test_untrusted_peer_denied_before_receipt_validation(self):
        self.f.peer=UNTRUSTED
        with self.assertRaisesRegex(GrantDenied,"UNTRUSTED_AGENT_PRINCIPAL"):
            self.reserve()

    def test_reject_action_not_permitted_under_accept_qa(self):
        p=self.request(signed_grant=self.f.env(action="reject"))
        with self.assertRaisesRegex(GrantDenied,"QA_GRANT_ACTION_NOT_ACCEPT"):
            self.reserve(p)
        self.assertEqual(self.f.ledger.state("a"*32),"UNKNOWN")

    def test_replay_denied_after_qa_success(self):
        p=self.request()
        self.reserve(p)
        with self.assertRaisesRegex(GrantDenied,"GRANT_ALREADY_SPENT"):
            self.reserve(p)

    def test_wrong_qa_key_denied(self):
        with self.assertRaisesRegex(GrantDenied,"QA_SIGNATURE_INVALID"):
            self.f.broker.reserve_with_independent_qa(
                self.request(), connected_pipe_handle=19,
                qa_key=b"wrong-qa-key-but-long-enough-00000000",
                qa_policy=POLICY)

if __name__=="__main__":
    unittest.main()
