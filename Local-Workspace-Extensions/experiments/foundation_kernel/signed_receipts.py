"""Ed25519 verification of bounded worker evidence receipts (isolated).

Public keys MUST be pinned out-of-band by a trusted host. This module never
generates or stores private keys, authenticates the MCP caller, approves work,
or asserts that an external side effect occurred. Signature verification is
a necessary but NOT sufficient part of trustworthy worker attestation.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Any

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    InvalidSignature = None
    Ed25519PublicKey = None

try:
    from .transition import canonical, validate_digest, validate_id
except ImportError:
    from transition import canonical, validate_digest, validate_id

RECEIPT_FIELDS = frozenset({
    "schema", "receipt_id", "run_id", "step_id", "request_id",
    "worker_id", "key_id", "role", "scope", "kind",
    "source_sha", "input_sha", "evidence_sha", "epoch", "issued_utc", "expires_utc",
})
SUPPORTED_KINDS = frozenset({"WORKER_RESULT", "DELIVERY_ACK", "QA_OBSERVATION"})
SCHEMA = "agent-signed-worker-receipt-v1"
MAX_RECEIPT_BYTES = 4096
MAX_VALIDITY_SECONDS = 600
CLOCK_SKEW_SECONDS = 20

class ReceiptError(ValueError):
    pass

@dataclass(frozen=True)
class PublicKeyGrant:
    key_id: str
    public_key: bytes
    worker_id: str
    role: str
    scope: str
    permitted_kinds: frozenset[str]
    revoked: bool = False

def _validate_grant(grant: PublicKeyGrant) -> None:
    if type(grant) is not PublicKeyGrant:
        raise ReceiptError("TRUST_GRANT_TYPE_INVALID")
    for label,value in (("KEY_ID",grant.key_id),
                        ("WORKER_ID",grant.worker_id),
                        ("ROLE",grant.role),("SCOPE",grant.scope)):
        try: validate_id(value,label)
        except ValueError as exc: raise ReceiptError(str(exc)) from exc
    if type(grant.public_key) is not bytes or len(grant.public_key)!=32:
        raise ReceiptError("TRUST_PUBLIC_KEY_INVALID")
    if (type(grant.permitted_kinds) is not frozenset or not grant.permitted_kinds
            or not grant.permitted_kinds.issubset(SUPPORTED_KINDS)):
        raise ReceiptError("TRUST_PERMISSIONS_INVALID")
    if type(grant.revoked) is not bool:
        raise ReceiptError("TRUST_REVOCATION_TYPE_INVALID")

class TrustStore:
    """Immutable in-process lookup. Does NOT bootstrap trust by itself."""
    def __init__(self, grants: tuple[PublicKeyGrant, ...]):
        if type(grants) is not tuple or len(grants)>1000:
            raise ReceiptError("TRUST_STORE_INVALID")
        records = {}
        used_public_keys = set()
        for grant in grants:
            _validate_grant(grant)
            if grant.key_id in records or grant.public_key in used_public_keys:
                raise ReceiptError("TRUST_DUPLICATE_KEY_OR_ID")
            records[grant.key_id]=grant
            used_public_keys.add(grant.public_key)
        self._grants = records

    def get(self,key_id: str) -> PublicKeyGrant:
        try: validate_id(key_id,"KEY_ID")
        except ValueError as exc: raise ReceiptError(str(exc)) from exc
        grant = self._grants.get(key_id)
        if grant is None or grant.revoked:
            raise ReceiptError("TRUST_KEY_UNKNOWN_OR_REVOKED")
        return grant

def _time(value: str) -> datetime:
    if (type(value) is not str or len(value)!=20 or
        value[-1]!="Z"):
        raise ReceiptError("RECEIPT_TIME_FORMAT_INVALID")
    try:
        date=datetime.strptime(value,"%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ReceiptError("RECEIPT_TIME_FORMAT_INVALID") from exc
    return date.replace(tzinfo=timezone.utc)

def _signed_bytes(document: dict[str,Any]) -> bytes:
    if type(document) is not dict or set(document)!=RECEIPT_FIELDS:
        raise ReceiptError("RECEIPT_SCHEMA_FIELDS_INVALID")
    try:
        encoded = canonical(document)
    except (ValueError,TypeError,OverflowError) as exc:
        raise ReceiptError("RECEIPT_JSON_INVALID") from exc
    if len(encoded)>MAX_RECEIPT_BYTES:
        raise ReceiptError("RECEIPT_TOO_LARGE")
    if document["schema"]!=SCHEMA:
        raise ReceiptError("RECEIPT_SCHEMA_VERSION_INVALID")
    for label in ("receipt_id","run_id","step_id","request_id",
                  "worker_id","key_id","role","scope"):
        try:
            validate_id(document[label],label.upper())
        except ValueError as exc:
            raise ReceiptError(str(exc)) from exc
    if type(document["kind"]) is not str or document["kind"] not in SUPPORTED_KINDS:
        raise ReceiptError("RECEIPT_KIND_INVALID")
    for sha_key in ("source_sha","input_sha","evidence_sha"):
        try:
            validate_digest(document[sha_key],required=True)
        except ValueError as exc:
            raise ReceiptError("RECEIPT_DIGEST_INVALID") from exc
    if type(document["epoch"]) is not int or not 1<=document["epoch"]<=2**31-1:
        raise ReceiptError("RECEIPT_EPOCH_INVALID")
    _time(document["issued_utc"])
    _time(document["expires_utc"])
    return encoded

def verify_signed_receipt(receipt: dict, trust: TrustStore, *,
                          now: datetime, run_id: str, step_id: str,
                          request_id: str, source_sha: str, input_sha: str,
                          evidence_sha: str, kind: str, epoch: int,
                          scope: str) -> dict:
    """Fail closed and verify signature against a previously pinned key grant.

    All expected values must be derived from independently trusted state,
    never copied from the received envelope itself.
    """
    if Ed25519PublicKey is None:
        raise ReceiptError("ED25519_BACKEND_UNAVAILABLE")
    if type(trust) is not TrustStore:
        raise ReceiptError("TRUST_STORE_REQUIRED")
    if (type(receipt) is not dict or set(receipt)!={"document","signature_b64"}):
        raise ReceiptError("SIGNED_ENVELOPE_INVALID")
    document=receipt["document"]
    raw=_signed_bytes(document)
    if (not isinstance(now,datetime) or now.tzinfo is None):
        raise ReceiptError("TRUSTED_CLOCK_REQUIRED")
    now=now.astimezone(timezone.utc)
    issued=_time(document["issued_utc"])
    expires=_time(document["expires_utc"])
    duration=(expires-issued).total_seconds()
    if not 0<duration<=MAX_VALIDITY_SECONDS:
        raise ReceiptError("RECEIPT_VALIDITY_WINDOW_INVALID")
    if (issued-now).total_seconds()>CLOCK_SKEW_SECONDS:
        raise ReceiptError("RECEIPT_FROM_FUTURE")
    if (now-expires).total_seconds()>0:
        raise ReceiptError("RECEIPT_EXPIRED")
    expected={
        "run_id":run_id, "step_id":step_id, "request_id":request_id,
        "source_sha":source_sha, "input_sha":input_sha,
        "evidence_sha":evidence_sha,
        "kind":kind,"epoch":epoch,"scope":scope,
    }
    if any(document[key]!=value for key,value in expected.items()):
        raise ReceiptError("RECEIPT_CONTEXT_MISMATCH")
    grant=trust.get(document["key_id"])
    if (document["worker_id"]!=grant.worker_id or
        document["role"]!=grant.role or document["scope"]!=grant.scope or
        document["kind"] not in grant.permitted_kinds):
        raise ReceiptError("RECEIPT_KEY_SCOPE_DENIED")
    signature=receipt["signature_b64"]
    if type(signature) is not str or len(signature)>128:
        raise ReceiptError("RECEIPT_SIGNATURE_FORMAT_INVALID")
    try:
        raw_sig=base64.b64decode(signature,validate=True)
    except (binascii.Error,ValueError) as exc:
        raise ReceiptError("RECEIPT_SIGNATURE_FORMAT_INVALID") from exc
    if len(raw_sig)!=64 or base64.b64encode(raw_sig).decode("ascii")!=signature:
        raise ReceiptError("RECEIPT_SIGNATURE_FORMAT_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(grant.public_key).verify(raw_sig,raw)
    except (InvalidSignature,ValueError) as exc:
        raise ReceiptError("RECEIPT_SIGNATURE_INVALID") from exc
    return {
        "ok":True, "signature_verified":True,
        "verified_key_id":grant.key_id,
        "worker_id":grant.worker_id,
        "role":grant.role,"kind":kind,
        "run_id":run_id, "step_id":step_id, "epoch":epoch,
        "evidence_sha":evidence_sha,
        "input_sha":input_sha,
        "external_effect_verified":False,
        "mcp_actor_authenticated":False,
        "permission_to_approve_or_deploy":False,
        "trusted_key_provenance_verified":False,
    }
