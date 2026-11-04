"""P085 privileged *finalization engine* for an ALREADY verified review.

Never export through legacy MCP. No candidate Python import or test execution
occurs in this module. It requires (1) OS-backed broker reservation, (2) signed
independent sandbox QA, (3) trusted pinned MergeIntent journal from protected
installation, (4) current DB lease + revision and source/spec hash equality.

The caller is a separate owner-installed Windows service. It MUST use a
trusted, immutable journal class: importing the main mutable project Python
is prohibited. This module is OFFLINE, not registered in running MCP.

Crash policy: RESERVED grant stays spent; any prepared merge journal stays
durable for explicit owner-only reconciliation. No automatic reset or replay.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .review_grants import GrantDenied, GrantLedger, SHA
from .review_qa_attestation import QATrustPolicy, verify_qa_receipt
from .review_target_snapshot import snapshot, _regular, MAX_FILE_BYTES


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ProtectedReviewFinalizer:
    def __init__(self, *, project: Path, database: Path, workspace: str,
                 owner_principals: dict, trusted_reviewer_actor: str,
                 ledger: GrantLedger, qa_key: bytes, qa_policy: QATrustPolicy,
                 journal_factory):
        self.project=Path(project)
        self.database=Path(database)
        self.workspace=workspace
        self.owner_principals=owner_principals
        self.actor=trusted_reviewer_actor
        self.ledger=ledger
        self.qa_key=qa_key
        self.qa_policy=qa_policy
        # Factory MUST be an owner-pinned version of review_merge.MergeIntent
        # from the protected installation, not an arbitrary live file import.
        if not callable(journal_factory):
            raise GrantDenied("TRUSTED_JOURNAL_FACTORY_MISSING")
        self._journal_factory=journal_factory

    def accept_reserved(self, request: dict, reservation: dict) -> dict:
        """Privileged-only commit; do not call with user-supplied reservation."""
        if (type(request) is not dict or
                set(request)!={"task_id","signed_grant","signed_qa_receipt"} or
                type(reservation) is not dict or
                reservation.get("state")!="RESERVED_REQUIRES_PRIVILEGED_QA" or
                reservation.get("task_id")!=request.get("task_id") or
                reservation.get("formal_review_performed") is not False or
                reservation.get("source_merged") is not False):
            raise GrantDenied("UNVERIFIED_BROKER_RESERVATION")
        if type(self.ledger) is not GrantLedger:
            raise GrantDenied("TRUSTED_LEDGER_MISSING")
        grant=request["signed_grant"]
        if (type(grant) is not dict or type(grant.get("payload")) is not dict):
            raise GrantDenied("INVALID_SIGNED_GRANT")
        nonce=grant["payload"].get("nonce")
        if self.ledger.state(nonce)!="RESERVED":
            raise GrantDenied("REVIEW_GRANT_NOT_RESERVED")
        if self.database.is_symlink() or not self.database.is_file():
            raise GrantDenied("TRUSTED_DB_UNAVAILABLE")
        # A write transaction serializes coordinator transitions while the
        # durable staged file journal records the file-system half.
        db=sqlite3.connect(self.database,timeout=20,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        with closing(db):
            db.execute("BEGIN IMMEDIATE")
            intent=None
            prepared=False
            committed=False
            try:
                row=db.execute("SELECT * FROM tasks WHERE task_id=?",
                               (request["task_id"],)).fetchone()
                if (row is None or row["status"]!="reviewing" or
                        type(row["revision"]) is not int or
                        not row["review_token"] or not row["review_until"]):
                    raise GrantDenied("REVIEW_LEASE_INVALID_AT_FINALIZATION")
                fresh=snapshot(
                    self.project,self.database,request["task_id"],
                    workspace=self.workspace,
                    owner_principals=self.owner_principals)
                if fresh.revision!=row["revision"]:
                    raise GrantDenied("REVIEW_REVISION_CHANGED")
                verified=self.ledger.inspect_signed_grant(
                    grant,authenticated_actor=self.actor,target=fresh)
                if verified["action"]!="accept":
                    raise GrantDenied("REVIEW_ACTION_NOT_ACCEPT")
                proof=verify_qa_receipt(
                    request["signed_qa_receipt"],self.qa_key,
                    target=fresh,policy=self.qa_policy)
                if proof["qa_receipt_sha256"]!=reservation.get("qa_receipt_sha256"):
                    raise GrantDenied("QA_RESERVATION_EVIDENCE_MISMATCH")
                current_main=json.loads(row["baseline"])
                declared=json.loads(row["deliverables"])
                if (type(current_main) is not dict or type(declared) is not list
                        or len(declared)!=len(set(declared)) or
                        not 1<=len(declared)<=6):
                    raise GrantDenied("INVALID_MERGE_DELIVERABLES")
                files=[]
                for name in sorted(declared):
                    if type(name) is not str:
                        raise GrantDenied("INVALID_MERGE_DELIVERABLES")
                    candidate=self.project/row["sandbox"]/name
                    _regular(self.project,row["sandbox"]+"/"+name,
                             max_bytes=MAX_FILE_BYTES)
                    files.append((name,candidate))
                intent=self._journal_factory(
                    self.project,self.project/".agent-team",request["task_id"])
                if intent.pending():
                    raise GrantDenied("MERGE_INTENT_ALREADY_PENDING")
                # Caller supplied receipt was signed before staging; compare
                # journal-staged bytes after prepare, BEFORE first target edit.
                journal=intent.prepare(files,current_main,row["revision"])
                prepared=True
                staged=[[e["name"],e["after_sha"]]
                        for e in sorted(journal["entries"],key=lambda e:e["name"])]
                if _sha(json.dumps(staged,separators=(",",":"),
                                   ensure_ascii=True).encode("ascii"))!=fresh.source_bundle_sha256:
                    raise GrantDenied("SOURCE_CHANGED_DURING_PREPARE")
                if _sha(_regular(self.project,row["spec_ref"].partition(" / ")[0],
                                 max_bytes=MAX_FILE_BYTES))!=fresh.spec_sha256:
                    raise GrantDenied("SPEC_CHANGED_DURING_PREPARE")
                # No untrusted code executes: apply copies bounded staged bytes
                # from the durable trusted journal into main.
                intent.apply()
                recorded=datetime.now(timezone.utc).isoformat(timespec="seconds")
                evidence=[
                    {"command": c["id"],"exit_code":c["exit_code"],
                     "evidence_sha256":c["evidence_sha256"],
                     "attestation_receipt_sha256":proof["qa_receipt_sha256"],
                     "runner_actor":self.qa_policy.runner_actor}
                    for c in request["signed_qa_receipt"]["payload"]["checks"]
                ]
                artifacts=[{"file":name,"sha256":sha} for name,sha in staged]
                qa_json=json.dumps(evidence,sort_keys=True)
                artifacts_json=json.dumps(artifacts,sort_keys=True)
                db.execute(
                    "INSERT INTO acceptance_records "
                    "(task_id,reviewer_role,recorded_at,qa_json,artifacts_json,spec_sha256) "
                    "VALUES(?,?,?,?,?,?)",
                    (request["task_id"],"architect-reviewer",recorded,
                     qa_json,artifacts_json,fresh.spec_sha256))
                updated=db.execute(
                    "UPDATE tasks SET status='done',review_token=NULL,"
                    "review_until=NULL,review_notes=?,reviewed_at=?,"
                    "activity_at=?,revision=revision+1 "
                    "WHERE task_id=? AND revision=? AND status='reviewing' "
                    "AND review_token=?",
                    ("P085 authenticated Windows reviewer; QA receipt "
                     + proof["qa_receipt_sha256"],recorded,recorded,
                     request["task_id"],row["revision"],row["review_token"]))
                if updated.rowcount!=1:
                    raise GrantDenied("TASK_CAS_FAILED")
                detail=json.dumps({
                    "qa_receipt_sha256":proof["qa_receipt_sha256"],
                    "grant_nonce_sha256":_sha(nonce.encode("ascii")),
                    "trusted_reviewer_actor":self.actor,
                    "files":[name for name,_ in staged]},sort_keys=True)
                db.execute(
                    "INSERT INTO events(task_id,role,action,detail,at) "
                    "VALUES(?,?,?,?,?)",
                    (request["task_id"],"architect-reviewer",
                     "accept",detail[:1600],recorded))
                db.execute("COMMIT")
                committed=True
            except BaseException:
                # COMMIT can succeed remotely then report a transport/OS error.
                # Never destroy the intent or mask the original failure with a
                # second "no transaction active" rollback error.
                if not committed and db.in_transaction:
                    try:
                        db.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                # Any staged journal remains durable for owner recovery.
                raise
        # The DB status is already committed: cleanup is best effort; a
        # retained intent must be inspected using owner recovery only.
        cleanup_needed=False
        try:
            if prepared:
                intent.cleanup()
        except (OSError,ValueError):
            cleanup_needed=True
        return {
            "task_id":request["task_id"],"status":"done",
            "qa_receipt_sha256":proof["qa_receipt_sha256"],
            "merged":[name for name,_ in staged],
            "retained_merge_intent":cleanup_needed,
            "independent_qa_verified":True,
        }
