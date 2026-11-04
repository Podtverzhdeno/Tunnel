"""Signed read-only reconciliation of a broker command after a lost reply.

This module NEVER issues retries or mutations. Absence of a recorded claim
must not be mistaken for permission to re-execute an external side effect.
The query is scoped to the SAME principal, run, step and epoch as a committed
command. For MARK_SEND_UNCERTAIN the historic schema does not bind the
command to a specific message: the response explicitly refuses that inference.
"""
from __future__ import annotations

from datetime import datetime, timezone

try:
    from .host_auth import verify_host_command
    from .signed_policy import verify_current_floor_locked, PolicySignatureError
    from .transition import validate_id
except ImportError:
    from host_auth import verify_host_command
    from signed_policy import verify_current_floor_locked, PolicySignatureError
    from transition import validate_id


class ReconciliationError(ValueError):
    pass


def reconcile_command(gate, *, envelope: dict, now: datetime,
                      scope: str, run_id: str, step_id: str,
                      request_id: str, epoch: int,
                      target_request_id: str, message_id: str) -> dict:
    """Read one signed status request in a consistent SQLite read snapshot.

    Unlike mutating requests the QUERY nonce is not consumed. This is safe
    only because QUERY_COMMAND_STATUS can never change process or task state.
    The trusted host must still enforce query rate limits and authorization.
    """
    validate_id(target_request_id, "TARGET_REQUEST_ID")
    validate_id(message_id, "MESSAGE_ID")
    args={"target_request_id":target_request_id,"message_id":message_id}
    identity=verify_host_command(
        envelope,gate.policy,now=now,action="QUERY_COMMAND_STATUS",
        run_id=run_id,step_id=step_id,request_id=request_id,
        epoch=epoch,scope=scope,arguments=args)
    db=gate.db
    if db.in_transaction:
        raise ReconciliationError("RECONCILE_REQUIRES_IDLE_CONNECTION")
    db.execute("BEGIN")
    try:
        if gate.active_policy_floor is not None:
            verify_current_floor_locked(db,gate.active_policy_floor)
            if (not isinstance(gate.active_policy_expiry_utc,datetime) or
                gate.active_policy_expiry_utc.tzinfo is None or
                datetime.now(timezone.utc)>gate.active_policy_expiry_utc):
                raise PolicySignatureError("SIGNED_POLICY_EXPIRED_DURING_SESSION")
        verified=gate.dispatch.audit_run(run_id)
        if not verified.get("ok"):
            raise ReconciliationError("RECONCILE_JOURNAL_DIVERGED")
        claim=db.execute(
            "SELECT principal_id,key_id,run_id,step_id,action,epoch "
            "FROM host_gate_claims WHERE request_id=?",
            (target_request_id,)).fetchone()
        safe={
            "ok":True, "query_signature_verified":True,
            "query_principal_id":identity["principal_id"],
            "run_id":run_id,"step_id":step_id,"epoch":epoch,
            "target_request_id":target_request_id,
            "automatic_retry_permitted":False,
            "external_effect_verified":False,
            "host_approval_granted":False,
            "changes_written":False,
            "production_mcp_integrated":False,
        }
        if claim is None:
            response={
                **safe,"recorded":False,
                "reconciliation_state":"NO_DURABLE_CLAIM_FOUND",
                "next_action":"MANUAL_RECONCILIATION_REQUIRED",
                "message_link_verified":False,
            }
        else:
            if (claim[0]!=identity["principal_id"] or
                claim[2:4]!=(run_id,step_id) or claim[5]!=epoch):
                raise ReconciliationError("RECONCILE_TARGET_NOT_AUTHORIZED")
            row=db.execute(
                "SELECT request_id,phase,epoch,intent_event_id,send_attempt_id "
                "FROM atomic_outbox WHERE message_id=? AND run_id=? AND step_id=?",
                (message_id,run_id,step_id)).fetchone()
            target=db.execute(
                "SELECT action,message_id FROM host_gate_command_targets "
                "WHERE request_id=?", (target_request_id,)).fetchone()
            if target is not None and target != (claim[4],message_id):
                raise ReconciliationError("RECONCILE_SIGNED_TARGET_MISMATCH")
            if claim[4]=="STAGE_INTENT":
                if row is None or row[0]!=target_request_id or row[2]!=epoch:
                    raise ReconciliationError("RECONCILE_ATOMIC_STAGE_LINK_MISSING")
                status="STAGE_INTENT_RECORDED"
                link=True
            elif claim[4]=="MARK_SEND_UNCERTAIN":
                if target is None:
                    # Historic rows lack a message binding; never invent one.
                    status="MARK_REQUEST_COMMITTED_MESSAGE_LINK_UNVERIFIED"
                    link=False
                elif (row is None or row[2]!=epoch or
                      row[1]!="SEND_UNCERTAIN" or not row[4]):
                    raise ReconciliationError("RECONCILE_ATOMIC_MARK_LINK_MISSING")
                else:
                    status="MARK_SEND_FENCE_RECORDED"
                    link=True
            else:
                raise ReconciliationError("RECONCILE_ACTION_UNKNOWN")
            response={
                **safe,"recorded":True,"committed_action":claim[4],
                "reconciliation_state":status,
                "message_link_verified":link,
                "outbox_phase":row[1] if row is not None and link else None,
                "next_action":"INSPECT_EFFECT_OR_RECEIPT_INDEPENDENTLY",
            }
        db.execute("COMMIT")
        return response
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
