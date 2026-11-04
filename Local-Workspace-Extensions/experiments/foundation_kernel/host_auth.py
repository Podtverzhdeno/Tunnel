"""Pure, fail-closed Ed25519 capability request verification.

This is an isolated reference protocol, NOT the running MCP host.
The actual host must pin keys and grants out of band. A role supplied by
an agent is never authority. No approval, deployment, shell or Git action.
"""
from __future__ import annotations
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    Ed25519PublicKey = None
    InvalidSignature = None

try:
    from .transition import canonical, validate_id, validate_digest
except ImportError:
    from transition import canonical, validate_id, validate_digest

DOMAIN = b"agent-host-capability-v1\x00"
SCHEMA = "agent-host-command-v1"
ACTIONS = frozenset({"STAGE_INTENT", "MARK_SEND_UNCERTAIN", "QUERY_COMMAND_STATUS"})
DOCUMENT_FIELDS = frozenset({
    "schema", "principal_id", "role", "key_id", "policy_revision", "scope", "run_id",
    "step_id", "epoch", "request_id", "nonce", "action", "arguments_sha",
    "issued_utc", "expires_utc",
})
MAX_VALIDITY_SECONDS = 90
CLOCK_SKEW_SECONDS = 10
MAX_DOCUMENT_BYTES = 4096

class HostAuthError(ValueError):
    pass

@dataclass(frozen=True)
class HostGrant:
    """Provisioned out of band; never created from a request payload."""
    principal_id: str
    role: str
    key_id: str
    public_key: bytes
    scope: str
    run_id: str
    step_id: str
    epoch: int
    policy_revision: int
    capabilities: frozenset[str]
    revoked: bool = False


def _id(value: Any, label: str) -> None:
    try:
        validate_id(value, label)
    except ValueError as exc:
        raise HostAuthError(str(exc)) from exc

def _epoch(value: Any, label: str) -> None:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise HostAuthError(label + "_INVALID")

def _utc(value: Any) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise HostAuthError("HOST_AUTH_TIMESTAMP_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HostAuthError("HOST_AUTH_TIMESTAMP_INVALID") from exc
    return parsed.replace(tzinfo=timezone.utc)

def _grant_check(grant: HostGrant) -> None:
    if type(grant) is not HostGrant:
        raise HostAuthError("HOST_GRANT_TYPE_INVALID")
    for label, value in (
        ("PRINCIPAL_ID", grant.principal_id), ("ROLE", grant.role),
        ("KEY_ID", grant.key_id),
        ("SCOPE", grant.scope), ("RUN_ID", grant.run_id),
        ("STEP_ID", grant.step_id),
    ):
        _id(value, label)
    _epoch(grant.epoch, "HOST_GRANT_EPOCH")
    _epoch(grant.policy_revision, "POLICY_REVISION")
    if type(grant.public_key) is not bytes or len(grant.public_key) != 32:
        raise HostAuthError("HOST_PUBLIC_KEY_INVALID")
    if (type(grant.capabilities) is not frozenset or
        not grant.capabilities or not grant.capabilities.issubset(ACTIONS)):
        raise HostAuthError("HOST_CAPABILITIES_INVALID")
    if type(grant.revoked) is not bool:
        raise HostAuthError("HOST_GRANT_REVOCATION_INVALID")

class HostPolicy:
    """Immutable key index; the host supplies pre-vetted grants."""
    def __init__(self, grants: tuple[HostGrant, ...]):
        if type(grants) is not tuple or len(grants) > 1000:
            raise HostAuthError("HOST_POLICY_INVALID")
        by_key: dict[str, HostGrant] = {}
        seen_public: set[bytes] = set()
        for grant in grants:
            _grant_check(grant)
            if grant.key_id in by_key or grant.public_key in seen_public:
                raise HostAuthError("HOST_POLICY_DUPLICATE_KEY")
            by_key[grant.key_id] = grant
            seen_public.add(grant.public_key)
        self._by_key = MappingProxyType(by_key)

    def get(self, key_id: str) -> HostGrant:
        _id(key_id, "KEY_ID")
        grant = self._by_key.get(key_id)
        if not grant or grant.revoked:
            raise HostAuthError("HOST_KEY_UNKNOWN_OR_REVOKED")
        return grant


def signed_document_bytes(document: dict) -> bytes:
    if type(document) is not dict or set(document) != DOCUMENT_FIELDS:
        raise HostAuthError("HOST_DOCUMENT_FIELDS_INVALID")
    try:
        payload = canonical(document)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HostAuthError("HOST_DOCUMENT_JSON_INVALID") from exc
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HostAuthError("HOST_DOCUMENT_OVERSIZED")
    if document["schema"] != SCHEMA:
        raise HostAuthError("HOST_DOCUMENT_SCHEMA_INVALID")
    for label in (
        "principal_id", "role", "key_id", "scope", "run_id", "step_id", "request_id"
    ):
        _id(document[label], label.upper())
    nonce = document["nonce"]
    if (type(nonce) is not str or len(nonce) != 32 or
        any(x not in "0123456789abcdef" for x in nonce)):
        raise HostAuthError("HOST_NONCE_INVALID")
    _epoch(document["epoch"], "HOST_REQUEST_EPOCH")
    _epoch(document["policy_revision"], "POLICY_REVISION")
    if type(document["action"]) is not str or document["action"] not in ACTIONS:
        raise HostAuthError("HOST_ACTION_NOT_EXPOSED")
    try:
        validate_digest(document["arguments_sha"], required=True)
    except ValueError as exc:
        raise HostAuthError("HOST_ARGUMENT_DIGEST_INVALID") from exc
    _utc(document["issued_utc"])
    _utc(document["expires_utc"])
    return DOMAIN + payload


def verify_host_command(envelope: dict, policy: HostPolicy, *, now: datetime,
                        action: str, run_id: str, step_id: str,
                        epoch: int, request_id: str, scope: str,
                        arguments: dict) -> dict:
    """Verify signed request against independently supplied dispatch context."""
    if Ed25519PublicKey is None:
        raise HostAuthError("HOST_CRYPTO_BACKEND_UNAVAILABLE")
    if type(policy) is not HostPolicy:
        raise HostAuthError("HOST_POLICY_REQUIRED")
    if type(envelope) is not dict or set(envelope) != {"document", "signature_b64"}:
        raise HostAuthError("HOST_ENVELOPE_INVALID")
    document = envelope["document"]
    body = signed_document_bytes(document)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise HostAuthError("HOST_CLOCK_REQUIRED")
    instant = now.astimezone(timezone.utc)
    issued, expires = _utc(document["issued_utc"]), _utc(document["expires_utc"])
    window = (expires - issued).total_seconds()
    if not 0 < window <= MAX_VALIDITY_SECONDS:
        raise HostAuthError("HOST_TIME_WINDOW_INVALID")
    if (issued - instant).total_seconds() > CLOCK_SKEW_SECONDS:
        raise HostAuthError("HOST_REQUEST_FROM_FUTURE")
    if instant > expires:
        raise HostAuthError("HOST_REQUEST_EXPIRED")
    for label, value in (("RUN_ID", run_id), ("STEP_ID", step_id),
                         ("REQUEST_ID", request_id), ("SCOPE", scope)):
        _id(value, label)
    _epoch(epoch, "HOST_REQUEST_EPOCH")
    if type(arguments) is not dict or set(arguments) != (
        {"message_id", "destination", "payload_sha"}
        if action == "STAGE_INTENT" else
        {"message_id", "attempt_id"} if action == "MARK_SEND_UNCERTAIN" else
        {"target_request_id", "message_id"} if action == "QUERY_COMMAND_STATUS" else set()
    ):
        raise HostAuthError("HOST_ACTION_ARGUMENTS_INVALID")
    if action not in ACTIONS:
        raise HostAuthError("HOST_ACTION_NOT_EXPOSED")
    for field in arguments:
        if field == "payload_sha":
            try:
                validate_digest(arguments[field], required=True)
            except ValueError as exc:
                raise HostAuthError("HOST_ARGUMENT_DIGEST_INVALID") from exc
        else:
            _id(arguments[field], "HOST_ARGUMENT_" + field.upper())
    try:
        arguments_bytes = canonical(arguments)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HostAuthError("HOST_ARGUMENT_JSON_INVALID") from exc
    if len(arguments_bytes) > 4096:
        raise HostAuthError("HOST_ARGUMENT_OVERSIZED")
    import hashlib
    digest = hashlib.sha256(arguments_bytes).hexdigest()
    if any(document[field] != value for field, value in (
        ("action", action), ("scope", scope), ("run_id", run_id),
        ("step_id", step_id), ("request_id", request_id),
        ("epoch", epoch), ("arguments_sha", digest),
    )):
        raise HostAuthError("HOST_COMMAND_CONTEXT_MISMATCH")
    grant = policy.get(document["key_id"])
    if (
        document["principal_id"] != grant.principal_id or
        document["role"] != grant.role or
        document["scope"] != grant.scope or
        document["run_id"] != grant.run_id or
        document["step_id"] != grant.step_id or
        document["epoch"] != grant.epoch or
        document["policy_revision"] != grant.policy_revision or
        action not in grant.capabilities
    ):
        raise HostAuthError("HOST_CAPABILITY_DENIED")
    signature = envelope["signature_b64"]
    if type(signature) is not str or len(signature) > 128:
        raise HostAuthError("HOST_SIGNATURE_FORMAT_INVALID")
    try:
        sig = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HostAuthError("HOST_SIGNATURE_FORMAT_INVALID") from exc
    if len(sig) != 64 or base64.b64encode(sig).decode("ascii") != signature:
        raise HostAuthError("HOST_SIGNATURE_FORMAT_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(grant.public_key).verify(sig, body)
    except (InvalidSignature, ValueError) as exc:
        raise HostAuthError("HOST_SIGNATURE_INVALID") from exc
    return {
        "verified": True, "principal_id": grant.principal_id,
        "trusted_role": grant.role,
        "key_id": grant.key_id, "policy_revision": grant.policy_revision,
        "run_id": run_id, "step_id": step_id, "epoch": epoch,
        "action": action, "request_id": request_id, "nonce": document["nonce"],
        "arguments_sha": digest,
        "authority_from_pinned_key": True,
        "mcp_host_integrated": False,
        "may_accept_review_or_deploy": False,
    }
