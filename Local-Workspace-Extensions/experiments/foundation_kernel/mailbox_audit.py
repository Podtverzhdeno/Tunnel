"""Fail-closed, read-only cross-store consistency check for the *test kernel*.

Journal, outbox, inbox and local receipts are four distinct trust domains.
Their SQLite reads are NOT a distributed snapshot. A matching local hash is
NOT an authenticated worker acknowledgement or real-world execution proof.
"""
from __future__ import annotations

import json

try:
    from .transition import validate_id
except ImportError:
    from transition import validate_id

MAX_AUDIT = 500

def audit_delivery(journal, sender, receiver, evidence_store,
                   run_id: str, *, limit: int = MAX_AUDIT) -> dict:
    validate_id(run_id, "RUN_ID")
    if type(limit) is not int or not 1 <= limit <= MAX_AUDIT:
        raise ValueError("DELIVERY_AUDIT_LIMIT_INVALID")
    base = {
        "run_id": run_id, "independent_worker_verified": False,
        "actor_authenticated": False, "can_approve": False,
        "can_replay_unknown_external_effect": False,
        "atomic_cross_database_snapshot": False,
    }
    verified = journal.verify(run_id)
    if not verified.get("ok"):
        return {**base, "ok": False, "decision": "BLOCKED",
                "findings": ["JOURNAL_INVALID:"+str(verified.get("error"))[:60]]}
    messages = sender.db.execute(
        "SELECT message_id,step_id,destination,payload_sha,phase,epoch,ack_sha "
        "FROM outbox WHERE run_id=? ORDER BY message_id LIMIT ?",
        (run_id, limit+1)).fetchall()
    if len(messages) > limit:
        return {**base, "ok": False, "decision": "BLOCKED",
                "findings": ["DELIVERY_AUDIT_LIMIT_EXCEEDED"]}
    events = journal.db.execute(
        "SELECT step_id,kind,epoch,evidence_sha,payload_json "
        "FROM events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    plan = {step_id:sha for step_id,kind,_,sha,_ in events if kind=="PLAN"}
    intents = {
        (step_id,epoch, json.loads(payload).get("message_id"),
         json.loads(payload).get("payload_sha"))
        for step_id,kind,epoch,_,payload in events
        if kind=="INTENT"
    }
    findings: list[str] = []
    uncertain = 0
    reported = 0
    for mid,step,destination,payload_sha,phase,epoch,ack in messages:
        state = verified["steps"].get(step)
        if state is None or step not in plan:
            findings.append(mid+":NO_VALID_PLAN")
            continue
        if phase == "STAGED":
            if state["state"] not in {"PLANNED","LEASED"}:
                findings.append(mid+":STAGED_AFTER_EFFECT_INTENT")
            continue
        if (step,epoch,mid,payload_sha) not in intents:
            findings.append(mid+":MISSING_MATCHING_INTENT")
            continue
        if state["epoch"] != epoch:
            findings.append(mid+":FENCING_EPOCH_DIVERGED")
            continue
        if state["state"] not in {
            "UNCERTAIN", "RECONCILIATION_REQUIRED",
            "RESULT_DOCUMENTED", "QA_OBSERVED"
        }:
            findings.append(mid+":JOURNAL_EFFECT_STATE_DIVERGED")
            continue
        uncertain += 1
        row=receiver.db.execute(
            "SELECT payload_sha,phase,epoch,receipt_sha "
            "FROM inbox WHERE consumer=? AND message_id=?",
            (destination,mid)).fetchone()
        if row is None:
            if phase=="ACK_REPORTED":
                findings.append(mid+":ACK_WITHOUT_INBOX")
            continue
        if row[0] != payload_sha:
            findings.append(mid+":INBOX_PAYLOAD_MISMATCH")
            continue
        if phase=="ACK_REPORTED":
            if ack is None:
                findings.append(mid+":ACK_DIGEST_MISSING")
                continue
            ack_receipt=evidence_store.verify(ack)
            if not ack_receipt.get("ok") or ack_receipt["source_sha"]!=plan[step]:
                findings.append(mid+":ACK_LOCAL_EVIDENCE_INVALID")
                continue
            reported += 1
        if row[1]=="RESULT_REPORTED":
            result_sha=row[3]
            receipt=evidence_store.verify(result_sha) if result_sha else {"ok":False}
            if not receipt.get("ok") or receipt["source_sha"]!=plan[step]:
                findings.append(mid+":WORKER_LOCAL_EVIDENCE_INVALID")
    decision = ("BLOCKED" if findings else
                "LOCAL_CONSISTENCY_UNVERIFIED_DELIVERY" if uncertain else
                "STAGED_ONLY")
    return {
        **base, "ok": not findings, "decision": decision,
        "messages_examined": len(messages), "send_intents_seen": uncertain,
        "reported_acks_with_local_evidence": reported,
        "findings": findings[:30],
        "findings_truncated":len(findings)>30,
    }
