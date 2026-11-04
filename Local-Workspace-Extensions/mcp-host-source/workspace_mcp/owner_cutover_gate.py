"""P085 protected manual cutover gate (operator assertion, NOT OS proof).

The original owner must perform actual cross-principal tests, then place a
short-lived acknowledgement in the inaccessible protected installation via
an elevated foreground console. This is a necessary explicit release gate,
NOT a substitute for real Windows ACL checks, reviewer identity, QA isolation
or source SHA verification. This module cannot create/approve acknowledgements.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .review_grants import GrantDenied, SHA
from .review_grant_broker import BrokerPolicy
from .review_qa_attestation import QATrustPolicy

SCHEMA = "p085-owner-verified-isolation-cutover-v1"
CHECKS = frozenset({
    "worker_cannot_write_coordinator_db",
    "worker_cannot_write_canonical_source",
    "worker_cannot_read_protected_keys",
    "worker_cannot_connect_reviewer_pipe",
    "reviewer_can_connect_reviewer_pipe",
    "qa_test_child_cannot_read_signing_key",
    "recovery_owner_has_tested_partial_merge",
})
FIELDS = frozenset({
    "schema","service_sid","reviewer_client_sid","untrusted_mcp_sid",
    "project_root","trusted_journal_sha256","qa_runner_environment_sha256",
    "recorded_at_utc","operator_checks"
})
MAX_BYTES = 8192
MAX_AGE_SECONDS = 24*3600


def service_private_root(service_root: Path) -> Path:
    """Stage-v2 separates immutable code/ from writable private_state/.

    Legacy fixtures outside the protected staging layout continue using
    p085_private under the service root. Only the owner-installed code/
    layout selects the sibling private_state path; no client parameter,
    environment variable or role string selects this path.
    """
    root=Path(service_root).resolve(strict=True)
    if root.name=="code":
        private=root.parent/"private_state"
        if private.is_symlink() or getattr(private,"is_junction",lambda:False)():
            raise GrantDenied("P085_PRIVATE_STATE_LINK_DENIED")
        return private
    return root/"p085_private"


def require_owner_cutover(service_root: Path, *, policy: BrokerPolicy,
                          project: Path, journal_sha256: str,
                          qa_policy: QATrustPolicy,
                          now: datetime | None = None) -> dict:
    """Never turns an assertion into cryptographic OS identity evidence."""
    path=service_private_root(service_root)/"owner-cutover.json"
    if (path.is_symlink() or getattr(path,"is_junction",lambda:False)()
            or not path.is_file() or path.stat().st_size>MAX_BYTES):
        raise GrantDenied("P085_OWNER_CUTOVER_NOT_ATTESTED")
    try:
        with path.open("rb") as stream:
            raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError("size")
        data=json.loads(raw.decode("utf-8"))
    except (OSError,ValueError,UnicodeError,RecursionError) as exc:
        raise GrantDenied("P085_OWNER_CUTOVER_INVALID") from exc
    if (type(data) is not dict or set(data)!=FIELDS
            or data["schema"]!=SCHEMA
            or data["service_sid"]!=policy.service_sid
            or data["reviewer_client_sid"]!=policy.reviewer_client_sid
            or data["untrusted_mcp_sid"]!=policy.untrusted_mcp_sid
            or data["project_root"]!=str(Path(project).resolve(strict=True))
            or data["trusted_journal_sha256"]!=journal_sha256
            or data["qa_runner_environment_sha256"]!=qa_policy.runner_environment_sha256):
        raise GrantDenied("P085_OWNER_CUTOVER_SCOPE_MISMATCH")
    checks=data["operator_checks"]
    if (type(checks) is not dict or set(checks)!=CHECKS
            or any(type(value) is not bool or not value for value in checks.values())):
        raise GrantDenied("P085_OWNER_CHECKS_INCOMPLETE")
    try:
        stamp=data["recorded_at_utc"]
        if type(stamp) is not str or len(stamp)>48:
            raise ValueError("bad stamp")
        created=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        moment=datetime.now(timezone.utc) if now is None else now
        if (created.tzinfo is None or moment.tzinfo is None
                or created>moment or
                not 0<= (moment-created).total_seconds()<=MAX_AGE_SECONDS):
            raise ValueError("stale")
    except (ValueError,TypeError) as exc:
        raise GrantDenied("P085_OWNER_CUTOVER_STALE_OR_INVALID") from exc
    return {
        "schema":SCHEMA,
        "owner_checks_asserted":True,
        "operator_verification_is_not_host_attestation":True,
        "recorded_at_utc":stamp,
        "ready_for_acceptance_candidate":True,
    }
