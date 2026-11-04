"""P085 signed independent QA receipt verification (verification-only).

No issuer, key generator, subprocess launcher, model-agent signature, runtime
module import, or merge here. The signer must be an OS-isolated *supervisor*
whose signing key is inaccessible to candidate tests and untrusted agents.
A receipt is evidence ONLY if a protected service verifies exact current task
hashes, configured runner/environment identity and positive required gates.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from .review_grants import ReviewTarget, GrantDenied, SHA, TASK, ACTOR, WORKSPACE

SCHEMA = "p085-independent-qa-receipt-v1"
ISSUER = "isolated-qa-supervisor-v1"
AUDIENCE = "p085-trusted-review-service-v1"
REQUIRED = ("unittest","spec_check","openspec_strict",
            "reuse_guard","architecture_guard","negative_qa")
RECEIPT_FIELDS = frozenset({
    "schema","issuer","audience","task_id","workspace","revision",
    "source_bundle_sha256","spec_sha256","review_lease_sha256",
    "runner_actor","runner_environment_sha256","issued_at","expires_at",
    "checks"
})
CHECK_FIELDS = frozenset({"id","exit_code","evidence_sha256"})
MAX_SIZE = 6000
MAX_QA_AGE_SECONDS = 900

@dataclass(frozen=True)
class QATrustPolicy:
    """Immutable, service-owned runner binding; never from a client request."""
    runner_actor: str
    runner_environment_sha256: str

    def validate(self):
        if (type(self.runner_actor) is not str or not ACTOR.fullmatch(self.runner_actor)
                or type(self.runner_environment_sha256) is not str
                or not SHA.fullmatch(self.runner_environment_sha256)):
            raise GrantDenied("QA_RUNNER_POLICY_INVALID")
        return True


def canonical(payload):
    if type(payload) is not dict or set(payload) != RECEIPT_FIELDS:
        raise GrantDenied("QA_RECEIPT_FIELDS_INVALID")
    try:
        raw=json.dumps(payload,sort_keys=True,separators=(",",":"),
                       ensure_ascii=True,allow_nan=False).encode("ascii")
    except (TypeError,ValueError,UnicodeError) as exc:
        raise GrantDenied("QA_RECEIPT_ENCODING_INVALID") from exc
    if len(raw)>MAX_SIZE:
        raise GrantDenied("QA_RECEIPT_OVERSIZED")
    return raw


def verify_qa_receipt(envelope: dict, key: bytes, *,
                      target: ReviewTarget, policy: QATrustPolicy,
                      now: int|None=None) -> dict:
    """Bounded proof verification. Never treats candidate-supplied PASS as QA."""
    moment=int(time.time()) if now is None else now
    if type(moment) is not int:
        raise GrantDenied("QA_CLOCK_INVALID")
    if type(key) is not bytes or len(key)<32:
        raise GrantDenied("QA_VERIFICATION_KEY_UNAVAILABLE")
    if type(target) is not ReviewTarget:
        raise GrantDenied("QA_TRUSTED_TARGET_MISSING")
    if type(policy) is not QATrustPolicy:
        raise GrantDenied("QA_TRUST_POLICY_MISSING")
    policy.validate()
    if type(envelope) is not dict or set(envelope) != {"payload","mac"}:
        raise GrantDenied("QA_ENVELOPE_INVALID")
    payload=envelope["payload"]
    raw=canonical(payload)
    mac=envelope["mac"]
    if type(mac) is not str or not SHA.fullmatch(mac):
        raise GrantDenied("QA_SIGNATURE_INVALID")
    calculated=hmac.new(key,raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac,calculated):
        raise GrantDenied("QA_SIGNATURE_INVALID")
    if (payload["schema"]!=SCHEMA or payload["issuer"]!=ISSUER
            or payload["audience"]!=AUDIENCE):
        raise GrantDenied("QA_ISSUER_OR_AUDIENCE_INVALID")
    # Reject accidental bool-as-int, float equality and forged flags.
    values=("revision","issued_at","expires_at")
    if any(type(payload.get(x)) is not int for x in values):
        raise GrantDenied("QA_SCOPE_INVALID")
    if (type(payload.get("workspace")) is not str
            or not WORKSPACE.fullmatch(payload["workspace"])
            or type(payload.get("task_id")) is not str
            or not TASK.fullmatch(payload["task_id"])
            or type(payload.get("runner_actor")) is not str
            or not ACTOR.fullmatch(payload["runner_actor"])):
        raise GrantDenied("QA_SCOPE_INVALID")
    if any(type(payload.get(x)) is not str or not SHA.fullmatch(payload[x])
           for x in ("source_bundle_sha256","spec_sha256","review_lease_sha256",
                     "runner_environment_sha256")):
        raise GrantDenied("QA_SCOPE_INVALID")
    issued=payload["issued_at"]
    expiry=payload["expires_at"]
    if (issued>moment+15 or issued<moment-MAX_QA_AGE_SECONDS
            or expiry<=moment or expiry<=issued
            or expiry>issued+MAX_QA_AGE_SECONDS):
        raise GrantDenied("QA_RECEIPT_EXPIRED")
    if (payload["workspace"]!=target.workspace
            or payload["task_id"]!=target.task_id
            or payload["revision"]!=target.revision
            or payload["source_bundle_sha256"]!=target.source_bundle_sha256
            or payload["spec_sha256"]!=target.spec_sha256
            or payload["review_lease_sha256"]!=target.review_lease_sha256):
        raise GrantDenied("QA_SOURCE_OR_LEASE_DRIFT")
    if (payload["runner_actor"]!=policy.runner_actor or
            payload["runner_environment_sha256"]!=policy.runner_environment_sha256):
        raise GrantDenied("QA_RUNNER_NOT_TRUSTED")
    if not target.review_lease_active or target.merge_intent_pending:
        raise GrantDenied("QA_TASK_NOT_REVIEWABLE")
    checks=payload.get("checks")
    if type(checks) is not list or len(checks)!=len(REQUIRED):
        raise GrantDenied("QA_CHECK_SET_INCOMPLETE")
    names=[]
    for check in checks:
        if type(check) is not dict or set(check)!=CHECK_FIELDS:
            raise GrantDenied("QA_CHECK_MALFORMED")
        if (type(check["id"]) is not str or check["id"] not in REQUIRED
                or type(check["exit_code"]) is not int or check["exit_code"]!=0
                or type(check["evidence_sha256"]) is not str
                or not SHA.fullmatch(check["evidence_sha256"])):
            raise GrantDenied("QA_CHECK_FAILED_OR_UNTRUSTED")
        names.append(check["id"])
    if set(names)!=set(REQUIRED) or len(set(names))!=len(names):
        raise GrantDenied("QA_CHECK_SET_INCOMPLETE")
    return {
        "task_id":target.task_id,
        "qa_receipt_sha256":hashlib.sha256(raw).hexdigest(),
        "qa_check_ids":list(REQUIRED),
        "valid_until":expiry,
        "formal_acceptance_performed":False,
    }
