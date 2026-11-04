"""P085 independent Windows named-pipe reviewer broker core (DENY by default).

This module is NOT a chat-facing MCP tool, an HTTP server, a signer, an owner
approval issuer or a code-merging implementation. It must run in a separate
OS-protected process. A public tool call supplies ONLY {task_id, signed_grant};
the reviewer's actor identity comes from Windows impersonation on a connected
named-pipe handle, and the current task state comes from protected SQLite and
local file bytes. The broker never trusts a client-supplied role, SID, lease,
revision, SHA, author, workspace or status.

Even an authorized reservation means RESERVED, not accepted. A separate
privileged finalizer with isolated QA remains necessary for a real merge.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .host_pipe_peer import SID, current_process_sid, named_pipe_client_sid
from .review_grants import GrantDenied, GrantLedger
from .review_target_snapshot import snapshot

@dataclass(frozen=True)
class BrokerPolicy:
    """Owner-installed immutable configuration, NEVER parsed from MCP arguments."""
    service_sid: str
    reviewer_client_sid: str
    untrusted_mcp_sid: str
    reviewer_actor: str
    workspace: str
    owner_principals: dict

    def validate(self):
        from .review_grants import ACTOR, WORKSPACE
        sids = (self.service_sid, self.reviewer_client_sid, self.untrusted_mcp_sid)
        if any(type(x) is not str or not SID.fullmatch(x) for x in sids):
            raise GrantDenied("INVALID_TRUSTED_WINDOWS_SID")
        if len(set(sids)) != 3:
            raise GrantDenied("OS_PRINCIPALS_NOT_SEPARATED")
        if (type(self.reviewer_actor) is not str
                or not ACTOR.fullmatch(self.reviewer_actor)
                or type(self.workspace) is not str
                or not WORKSPACE.fullmatch(self.workspace)):
            raise GrantDenied("INVALID_TRUSTED_BROKER_CONFIG")
        expected = {f"developer-{i}" for i in range(1, 5)}
        if (type(self.owner_principals) is not dict
                or set(self.owner_principals) != expected
                or any(type(p) is not str or not ACTOR.fullmatch(p)
                       for p in self.owner_principals.values())
                or self.reviewer_actor in self.owner_principals.values()):
            raise GrantDenied("UNTRUSTED_AUTHOR_MAPPING")
        return True


class ReviewGrantBroker:
    """Reservation-only gate. Separate OS service must own this object."""

    def __init__(self, policy: BrokerPolicy, *, project: Path, coordinator_db: Path,
                 ledger: GrantLedger, peer_sid_resolver: Callable | None = None,
                 service_sid_resolver: Callable | None = None,
                 snapshot_source: Callable | None = None):
        if type(policy) is not BrokerPolicy:
            raise GrantDenied("BROKER_POLICY_UNAVAILABLE")
        policy.validate()
        self._policy = policy
        self._project = Path(project)
        self._coordinator_db = Path(coordinator_db)
        self._ledger = ledger
        if not isinstance(ledger, GrantLedger):
            raise GrantDenied("TRUSTED_LEDGER_MISSING")
        # Resolvers are constructor dependencies, never supplied per request.
        # Real deployment MUST use Windows native functions and protected DB.
        self._peer_sid = peer_sid_resolver or named_pipe_client_sid
        self._server_sid = service_sid_resolver or current_process_sid
        self._snapshot = snapshot_source or snapshot
        if self._server_sid() != policy.service_sid:
            raise GrantDenied("BROKER_PROCESS_IDENTITY_MISMATCH")

    def reserve(self, request: dict, *, connected_pipe_handle: int,
                now=None) -> dict:
        if (type(request) is not dict or set(request) != {"task_id", "signed_grant"}
                or type(request["task_id"]) is not str):
            raise GrantDenied("INVALID_BROKER_REQUEST")
        # SID is retrieved from OS pipe impersonation. Actor name cannot be
        # read from user JSON, headers, or a command-line role string.
        peer = self._peer_sid(connected_pipe_handle)
        if type(peer) is not str or not SID.fullmatch(peer):
            raise GrantDenied("PEER_AUTHENTICATION_FAILED")
        if peer == self._policy.untrusted_mcp_sid:
            raise GrantDenied("UNTRUSTED_AGENT_PRINCIPAL")
        if peer != self._policy.reviewer_client_sid:
            raise GrantDenied("REVIEWER_PRINCIPAL_NOT_AUTHORIZED")
        actual = self._snapshot(self._project, self._coordinator_db,
                                request["task_id"],
                                workspace=self._policy.workspace,
                                owner_principals=self._policy.owner_principals)
        # No QA/accept or source mutation here. A RESERVED crash intentionally
        # requires owner recovery rather than unsafe repeat execution.
        receipt = self._ledger.reserve(
            request["signed_grant"],
            authenticated_actor=self._policy.reviewer_actor,
            target=actual, now=now)
        return {
            "task_id": receipt["task_id"],
            "state": receipt["status"],
            "one_shot": True,
            "formal_review_performed": False,
            "source_merged": False,
            "pending_owner_recovery_on_crash": True,
        }

    def reserve_with_independent_qa(
            self, request: dict, *, connected_pipe_handle: int,
            qa_key: bytes, qa_policy, now=None) -> dict:
        """Authorize one-shot RESERVED only after independent signed QA.

        Caller-supplied claims do not supply trust. The protected service
        owns qa_key and qa_policy; no candidate Python is executed here.
        This is NOT an acceptance or source-merging endpoint.
        """
        from .review_qa_attestation import (
            QATrustPolicy, verify_qa_receipt,
        )
        if (type(request) is not dict or
                set(request) != {"task_id", "signed_grant", "signed_qa_receipt"} or
                type(request["task_id"]) is not str):
            raise GrantDenied("INVALID_QA_BROKER_REQUEST")
        if type(qa_policy) is not QATrustPolicy:
            raise GrantDenied("QA_TRUST_POLICY_MISSING")
        # Authenticate actual OS peer BEFORE reading any state or spending
        # grant nonces. Real deployment uses native named-pipe impersonation.
        peer = self._peer_sid(connected_pipe_handle)
        if type(peer) is not str or not SID.fullmatch(peer):
            raise GrantDenied("PEER_AUTHENTICATION_FAILED")
        if peer == self._policy.untrusted_mcp_sid:
            raise GrantDenied("UNTRUSTED_AGENT_PRINCIPAL")
        if peer != self._policy.reviewer_client_sid:
            raise GrantDenied("REVIEWER_PRINCIPAL_NOT_AUTHORIZED")
        target = self._snapshot(
            self._project, self._coordinator_db, request["task_id"],
            workspace=self._policy.workspace,
            owner_principals=self._policy.owner_principals)
        now_seconds = now
        qa = verify_qa_receipt(
            request["signed_qa_receipt"], qa_key, target=target,
            policy=qa_policy, now=now_seconds)
        # Strict acceptance path requires both independent attestation
        # and an owner-granted, task/lease/source-specific signature.
        proof = self._ledger.inspect_signed_grant(
            request["signed_grant"],
            authenticated_actor=self._policy.reviewer_actor,
            target=target, now=now_seconds)
        if proof["action"] != "accept":
            raise GrantDenied("QA_GRANT_ACTION_NOT_ACCEPT")
        reserved = self._ledger.reserve(
            request["signed_grant"],
            authenticated_actor=self._policy.reviewer_actor,
            target=target, now=now_seconds)
        return {
            "task_id": reserved["task_id"],
            "state": reserved["status"],
            "qa_receipt_sha256": qa["qa_receipt_sha256"],
            "qa_check_ids": qa["qa_check_ids"],
            "one_shot": True,
            "formal_review_performed": False,
            "source_merged": False,
        }
