"""Read-only replay audit linking execution events to immutable evidence.

A locally matching digest is provenance consistency, NOT independent reviewer
acceptance, external side-effect attestation or permission to deploy.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Any

try:
    from .journal import Journal
    from .evidence_store import EvidenceStore
    from .transition import validate_id
except ImportError:
    from journal import Journal
    from evidence_store import EvidenceStore
    from transition import validate_id

EVIDENCE_EVENTS=frozenset({"RESULT","RECONCILED_RESULT","QA_OBSERVED"})

def audit_run(journal: Journal, evidence_store: EvidenceStore, run_id: str,
              max_events: int = 10000) -> dict:
    """No file writes, no work execution and no review approval."""
    validate_id(run_id,"RUN_ID")
    verified=journal.verify(run_id,max_events=max_events)
    if not verified.get("ok"):
        return {"ok":False,"decision":"BLOCKED",
                "reason":"JOURNAL_INVALID","detail":verified.get("error"),
                "can_accept":False,"can_retry_external_effect":False}
    # Journal.validate has already checked every event and the cached state.
    rows=journal.db.execute(
        "SELECT step_id,kind,evidence_sha FROM events WHERE run_id=? "
        "ORDER BY id LIMIT ?", (run_id,max_events)
    ).fetchall()
    planned={}
    findings=[]
    verified_count=0
    for step_id,kind,sha in rows:
        if kind=="PLAN":
            planned[step_id]=sha
        if kind not in EVIDENCE_EVENTS:
            continue
        check=evidence_store.verify(sha)
        if not check.get("ok"):
            findings.append({"step_id":step_id,"reason":"EVIDENCE_MISSING_OR_INVALID",
                             "evidence_sha":sha})
            continue
        if check["source_sha"]!=planned.get(step_id):
            findings.append({"step_id":step_id,"reason":"SOURCE_DIGEST_MISMATCH",
                             "evidence_sha":sha})
            continue
        verified_count+=1
    return {
        "ok":len(findings)==0,
        "decision":"LOCAL_EVIDENCE_CONSISTENT" if not findings else "BLOCKED",
        "run_id":run_id,
        "evidence_receipts_validated":verified_count,
        "findings":findings[:25],
        "truncated_findings":len(findings)>25,
        "steps":verified["steps"],
        "external_effects_attested":False,
        "host_identity_authenticated":False,
        "can_accept":False,
        "can_retry_external_effect":False,
    }
