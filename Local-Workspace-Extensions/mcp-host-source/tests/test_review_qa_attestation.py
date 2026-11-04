"""P085 independent QA receipt negative tests; offline, disposable keys."""
from __future__ import annotations

import hashlib
import hmac
import time
import unittest
from dataclasses import replace

from workspace_mcp.review_grants import ReviewTarget, GrantDenied
from workspace_mcp.review_qa_attestation import (
    AUDIENCE, ISSUER, REQUIRED, SCHEMA, QATrustPolicy,
    canonical, verify_qa_receipt,
)

KEY=b"qa-test-only-key-strong-length-not-production"
POLICY=QATrustPolicy("qa:isolated-supervisor","f"*64)
NOW=1793610000
TARGET=ReviewTarget("projects","P152",8,"a"*64,"b"*64,"c"*64,True,False,"developer:one")


def payload():
    return {
        "schema":SCHEMA,"issuer":ISSUER,"audience":AUDIENCE,
        "workspace":"projects","task_id":"P152","revision":8,
        "spec_sha256":"a"*64,"source_bundle_sha256":"b"*64,
        "review_lease_sha256":"c"*64,
        "runner_actor":POLICY.runner_actor,
        "runner_environment_sha256":POLICY.runner_environment_sha256,
        "issued_at":NOW-15,"expires_at":NOW+70,
        "checks":[{"id":name,"exit_code":0,"evidence_sha256":hex(i+1)[2:]*64}
                  for i,name in enumerate(REQUIRED)],
    }


def sign(p):
    return {"payload":p,"mac":hmac.new(KEY,canonical(p),hashlib.sha256).hexdigest()}


def check(envelope=None,target=TARGET,policy=POLICY):
    return verify_qa_receipt(envelope if envelope is not None else sign(payload()),
                             KEY,target=target,policy=policy,now=NOW)


class QAProofTests(unittest.TestCase):
    def test_valid_signed_receipt_is_not_acceptance(self):
        r=check()
        self.assertEqual(r["task_id"],"P152")
        self.assertEqual(len(r["qa_check_ids"]),6)
        self.assertFalse(r["formal_acceptance_performed"])

    def test_cannot_forge_pass_by_editing_signed_fields(self):
        proof=sign(payload())
        proof["payload"]["checks"][0]["exit_code"]=0  # still same
        proof["payload"]["checks"][0]["evidence_sha256"]="e"*64
        with self.assertRaisesRegex(GrantDenied,"QA_SIGNATURE_INVALID"):
            check(proof)

    def test_missing_negative_check_never_passes(self):
        p=payload()
        p["checks"]=p["checks"][:-1]
        with self.assertRaisesRegex(GrantDenied,"QA_CHECK_SET_INCOMPLETE"):
            check(sign(p))

    def test_duplicate_check_never_counts_as_missing_one(self):
        p=payload()
        p["checks"][-1]=dict(p["checks"][0])
        with self.assertRaisesRegex(GrantDenied,"QA_CHECK_SET_INCOMPLETE"):
            check(sign(p))

    def test_all_positive_exit_codes_required(self):
        p=payload()
        p["checks"][4]["exit_code"]=1
        with self.assertRaisesRegex(GrantDenied,"QA_CHECK_FAILED_OR_UNTRUSTED"):
            check(sign(p))

    def test_bool_zero_and_float_exit_codes_are_not_valid(self):
        for value in (False,0.0,"0"):
            with self.subTest(value=value):
                p=payload()
                p["checks"][2]["exit_code"]=value
                with self.assertRaisesRegex(GrantDenied,"QA_CHECK_FAILED_OR_UNTRUSTED"):
                    check(sign(p))

    def test_spec_source_lease_revision_and_task_changes_rejected(self):
        for changed in ({"spec_sha256":"d"*64},
                        {"source_bundle_sha256":"d"*64},
                        {"review_lease_sha256":"d"*64},
                        {"revision":9},{"task_id":"P153"},{"workspace":"another"}):
            with self.subTest(changed=changed):
                t=replace(TARGET,**changed)
                with self.assertRaisesRegex(GrantDenied,"QA_SOURCE_OR_LEASE_DRIFT"):
                    check(target=t)

    def test_bad_runner_not_trusted(self):
        for pol in (QATrustPolicy("qa:other","f"*64),
                    QATrustPolicy(POLICY.runner_actor,"e"*64)):
            with self.assertRaisesRegex(GrantDenied,"QA_RUNNER_NOT_TRUSTED"):
                check(policy=pol)

    def test_unknown_issuer_or_audience_rejected(self):
        for change in ({"issuer":"worker-model"},{"audience":"chat-mcp"}):
            p={**payload(),**change}
            with self.assertRaisesRegex(GrantDenied,"QA_ISSUER_OR_AUDIENCE_INVALID"):
                check(sign(p))

    def test_expiry_future_issued_and_too_long(self):
        for change in ({"expires_at":NOW},
                       {"issued_at":NOW+17},
                       {"issued_at":NOW-1000},
                       {"expires_at":NOW+901},
                       {"issued_at":False}):
            with self.subTest(change=change):
                p={**payload(),**change}
                with self.assertRaises(GrantDenied):
                    check(sign(p))

    def test_stale_review_lease_cannot_verify(self):
        with self.assertRaisesRegex(GrantDenied,"QA_TASK_NOT_REVIEWABLE"):
            check(target=replace(TARGET,review_lease_active=False))
        with self.assertRaisesRegex(GrantDenied,"QA_TASK_NOT_REVIEWABLE"):
            check(target=replace(TARGET,merge_intent_pending=True))

    def test_added_fields_rejected(self):
        p=payload()
        p["role"]="architect-reviewer"
        with self.assertRaisesRegex(GrantDenied,"QA_RECEIPT_FIELDS_INVALID"):
            check({"payload":p,"mac":"e"*64})

    def test_malformed_check_fields_rejected(self):
        p=payload()
        p["checks"][0]["note"]="looks green"
        with self.assertRaisesRegex(GrantDenied,"QA_CHECK_MALFORMED"):
            check(sign(p))

    def test_key_custody_required(self):
        with self.assertRaisesRegex(GrantDenied,"QA_VERIFICATION_KEY_UNAVAILABLE"):
            verify_qa_receipt(sign(payload()),b"",target=TARGET,
                              policy=POLICY,now=NOW)

if __name__=="__main__":
    unittest.main()
