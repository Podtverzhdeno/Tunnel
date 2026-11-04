"""Bind signed worker receipts to actual atomic-dispatch journal intents.

Only an offline integration reference. The trust store, clock, and expected
project scope MUST originate from an independently authenticated host.
This does NOT attest real effects and may not auto-approve agent output.
"""
from __future__ import annotations
from datetime import datetime

try:
    from .atomic_dispatch import AtomicDispatch
    from .receipt_ledger import ReceiptLedger
    from .signed_receipts import TrustStore
    from .transition import validate_id
except ImportError:
    from atomic_dispatch import AtomicDispatch
    from receipt_ledger import ReceiptLedger
    from signed_receipts import TrustStore
    from transition import validate_id

class WorkerAttestationError(ValueError):
    pass

def admit_worker_receipt(dispatch: AtomicDispatch, ledger: ReceiptLedger,
                         evidence_store, trust: TrustStore, *,
                         message_id: str, receipt: dict,
                         trusted_scope: str, now: datetime) -> dict:
    """Verify an outbox-correlated result and persist unique receipt claim.

    It reads the authoritative local PLAN and sender outbox; no request
    identifiers or source SHA are trusted merely because the worker
    placed them inside a signed envelope.
    """
    if type(dispatch) is not AtomicDispatch or type(ledger) is not ReceiptLedger:
        raise WorkerAttestationError("KERNEL_COMPONENT_REQUIRED")
    validate_id(message_id,"MESSAGE_ID")
    validate_id(trusted_scope,"SCOPE")
    row=dispatch.db.execute(
        "SELECT run_id,step_id,request_id,destination,payload_sha,epoch,phase "
        "FROM atomic_outbox WHERE message_id=?", (message_id,)).fetchone()
    if row is None:
        raise WorkerAttestationError("ATOMIC_OUTBOX_MESSAGE_NOT_FOUND")
    run_id,step_id,request_id,destination,input_sha,epoch,phase=row
    if phase!="SEND_UNCERTAIN":
        raise WorkerAttestationError("MESSAGE_NOT_SENT_OR_UNCERTAIN")
    report=dispatch.audit_run(run_id)
    if not report.get("ok"):
        raise WorkerAttestationError("JOURNAL_OR_ATOMIC_LINK_INVALID")
    plan=dispatch.db.execute(
        "SELECT evidence_sha FROM events WHERE run_id=? AND step_id=? "
        "AND kind='PLAN'",(run_id,step_id)).fetchall()
    if len(plan)!=1:
        raise WorkerAttestationError("PLAN_SOURCE_MISSING_OR_AMBIGUOUS")
    source_sha=plan[0][0]
    if (type(receipt) is not dict or
        type(receipt.get("document")) is not dict):
        raise WorkerAttestationError("WORKER_RECEIPT_INVALID")
    document=receipt["document"]
    if document.get("worker_id") != destination:
        raise WorkerAttestationError("WORKER_DESTINATION_MISMATCH")
    evidence_sha=document.get("evidence_sha")
    result=ledger.admit(
        receipt,trust,evidence_store,
        now=now,run_id=run_id,step_id=step_id,request_id=request_id,
        source_sha=source_sha,input_sha=input_sha,evidence_sha=evidence_sha,
        kind="WORKER_RESULT",epoch=epoch,scope=trusted_scope)
    return {**result, "message_id":message_id,
            "signed_input_bound_to_atomic_outbox":True,
            "signed_source_bound_to_journal_plan":True,
            "external_effect_verified":False,
            "independent_reviewer_accepted":False,
            "host_permission_enforced":False,
            "can_accept_or_deploy":False}
