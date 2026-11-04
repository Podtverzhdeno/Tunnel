"""Fail-closed execution step protocol. Pure validation, NO task authority.

It is a reference protocol for offline tests. A role string or a digest is
NOT host-verified identity, approval or a side-effect receipt.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
ALLOWED = frozenset({"PLAN", "LEASE", "RELEASE", "INTENT", "RESULT",
                     "RECONCILIATION", "RECONCILED_RESULT", "QA_OBSERVED",
                     "CANCEL"})
FORBIDDEN = frozenset({"password", "secret", "token", "api_key", "authorization",
                       "cookie", "private_key", "bearer", "access_token",
                       "refresh_token", "lease_token"})
GENESIS = "0" * 64

class ProtocolError(ValueError):
    pass

@dataclass(frozen=True)
class Step:
    state: str = "NEW"
    epoch: int = 0

def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def validate_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ProtocolError(field + "_INVALID")

def validate_digest(value: str | None, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ProtocolError("EVIDENCE_DIGEST_INVALID")

def _has_secret_key(obj: object, depth: int = 0) -> bool:
    if depth > 6:
        raise ProtocolError("PAYLOAD_TOO_DEEP")
    if isinstance(obj, dict):
        return any(
            key.casefold() in FORBIDDEN or _has_secret_key(v, depth + 1)
            for key, v in obj.items()
            if isinstance(key, str)
        )
    if isinstance(obj, (list, tuple)):
        return any(_has_secret_key(v, depth + 1) for v in obj)
    return False

def validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ProtocolError("PAYLOAD_NOT_OBJECT")
    if not all(isinstance(k, str) for k in payload):
        raise ProtocolError("PAYLOAD_KEYS_INVALID")
    try:
        encoded = canonical(payload)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ProtocolError("PAYLOAD_INVALID_JSON") from exc
    if len(encoded) > 4096:
        raise ProtocolError("PAYLOAD_TOO_LARGE")
    if _has_secret_key(payload):
        raise ProtocolError("PAYLOAD_CONTAINS_SECRET_KEY")

def next_step(current: Step, kind: str, epoch: int,
              evidence_sha: str | None) -> Step:
    if kind not in ALLOWED:
        raise ProtocolError("EVENT_KIND_DENIED")
    if type(epoch) is not int or not 0 <= epoch <= 2**31 - 1:
        raise ProtocolError("EPOCH_INVALID")
    validate_digest(evidence_sha, required=kind in {
        "PLAN", "RESULT", "RECONCILED_RESULT", "QA_OBSERVED"
    })
    if kind in {"PLAN", "CANCEL"}:
        if kind == "PLAN" and current.state == "NEW" and epoch == 0:
            return Step("PLANNED", 0)
        if kind == "CANCEL" and current.state == "PLANNED" and epoch == current.epoch:
            return Step("CANCELLED", epoch)
        raise ProtocolError("INVALID_STEP_TRANSITION")
    if kind == "LEASE":
        if current.state != "PLANNED" or epoch <= current.epoch:
            raise ProtocolError("STALE_OR_INVALID_LEASE")
        return Step("LEASED", epoch)
    if epoch != current.epoch or epoch == 0:
        raise ProtocolError("FENCED_EPOCH_MISMATCH")
    mapping = {
        ("LEASED", "RELEASE"): "PLANNED",
        ("LEASED", "INTENT"): "UNCERTAIN",
        ("UNCERTAIN", "RESULT"): "RESULT_DOCUMENTED",
        ("UNCERTAIN", "RECONCILIATION"): "RECONCILIATION_REQUIRED",
        ("RECONCILIATION_REQUIRED", "RECONCILED_RESULT"): "RESULT_DOCUMENTED",
        ("RESULT_DOCUMENTED", "QA_OBSERVED"): "QA_OBSERVED",
    }
    state = mapping.get((current.state, kind))
    if state is None:
        raise ProtocolError("INVALID_STEP_TRANSITION")
    return Step(state, epoch)

def recovery_action(step: Step) -> str:
    return {
        "NEW": "NO_WORK_PLANNED",
        "PLANNED": "SAFE_TO_REQUEST_NEW_LEASE",
        "LEASED": "CHECK_LEASE_WITH_TRUSTED_COORDINATOR",
        "UNCERTAIN": "RECONCILE_EXTERNAL_SIDE_EFFECT_BEFORE_RETRY",
        "RECONCILIATION_REQUIRED": "MANUAL_RECONCILIATION_ONLY",
        "RESULT_DOCUMENTED": "VERIFY_RECEIPT_INDEPENDENTLY",
        "QA_OBSERVED": "INDEPENDENT_REVIEW_REQUIRED",
        "CANCELLED": "NO_RETRY_CANCELLED",
    }[step.state]

def chain_hash(envelope: dict) -> str:
    return hashlib.sha256(canonical(envelope)).hexdigest()
