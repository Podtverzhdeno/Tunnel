"""Single-SQLite atomic INTENT + outbox reference implementation.

No network calls, no code execution, no host identity verification, and no
connection to the live multi-agent queue. The journal and outbox share ONE
SQLite connection: no durable state can contain only one side of staging.
This does not solve external exactly-once delivery.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

try:
    from .journal import Journal, JournalError
    from .transition import ProtocolError, canonical, validate_id, validate_digest
except ImportError:
    from journal import Journal, JournalError
    from transition import ProtocolError, canonical, validate_id, validate_digest

PROTOCOL = "atomic-journal-outbox-v1"
MAX_OUTBOX = 10000
MAX_INSPECT = 500


class AtomicDispatchError(ValueError):
    pass


def _bounded_epoch(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise AtomicDispatchError("DISPATCH_EPOCH_INVALID")


class AtomicDispatch:
    """Atomic journal+outbox staging with idempotent message identity."""

    def __init__(self, journal: Journal):
        if not isinstance(journal, Journal):
            raise AtomicDispatchError("JOURNAL_REQUIRED")
        self.journal = journal
        self.db = journal.db
        if self.db.in_transaction:
            raise AtomicDispatchError("INITIALIZE_OUTSIDE_TRANSACTION")
        existing = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        tables = {"dispatch_meta", "atomic_outbox"}
        if existing.intersection(tables):
            if not tables.issubset(existing):
                raise AtomicDispatchError("INCOMPLETE_ATOMIC_SCHEMA")
            try:
                marker = self.db.execute(
                    "SELECT value FROM dispatch_meta WHERE key='protocol'").fetchone()
            except sqlite3.DatabaseError as exc:
                raise AtomicDispatchError("ATOMIC_SCHEMA_INVALID") from exc
            if marker != (PROTOCOL,):
                raise AtomicDispatchError("ATOMIC_PROTOCOL_MISMATCH")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS dispatch_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS atomic_outbox (
                message_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                payload_sha TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                intent_event_id INTEGER NOT NULL UNIQUE,
                phase TEXT NOT NULL CHECK(phase IN
                    ('INTENT_STAGED', 'SEND_UNCERTAIN')),
                send_attempt_id TEXT,
                FOREIGN KEY(intent_event_id) REFERENCES events(id),
                UNIQUE(run_id, request_id));
            CREATE INDEX IF NOT EXISTS atomic_outbox_run
                ON atomic_outbox(run_id, message_id);
        """)
        if not existing.intersection(tables):
            self.db.execute(
                "INSERT INTO dispatch_meta(key,value) VALUES('protocol',?)",
                (PROTOCOL,))

    def stage_intent(self, run_id: str, step_id: str, request_id: str,
                     message_id: str, destination: str, payload_sha: str,
                     epoch: int) -> dict:
        """Add journal INTENT and outbox row in one BEGIN IMMEDIATE/COMMIT.

        The caller MUST already have recorded a legitimate PLAN and LEASE.
        A duplicate request is safe only when BOTH journal and outbox agree.
        This method never calls the transport or actor coordinator.
        """
        for label, val in (("RUN_ID", run_id), ("STEP_ID", step_id),
                           ("REQUEST_ID", request_id), ("MESSAGE_ID", message_id),
                           ("DESTINATION", destination)):
            validate_id(val, label)
        validate_digest(payload_sha, required=True)
        _bounded_epoch(epoch)
        payload = {"message_id":message_id, "destination":destination,
                   "payload_sha":payload_sha, "atomic_dispatch":1}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if not self.audit_run(run_id).get("ok"):
                raise AtomicDispatchError("PREEXISTING_ATOMIC_DISPATCH_DIVERGED")
            stored = self.db.execute(
                "SELECT message_id,run_id,step_id,request_id,destination,payload_sha,"
                "epoch,intent_event_id,phase FROM atomic_outbox "
                "WHERE message_id=? OR (run_id=? AND request_id=?)",
                (message_id,run_id,request_id)).fetchall()
            if len(stored)>1:
                raise AtomicDispatchError("REQUEST_OR_MESSAGE_COLLISION")
            if stored:
                row = stored[0]
                if row[:7] != (message_id,run_id,step_id,request_id,destination,payload_sha,epoch):
                    raise AtomicDispatchError("IDEMPOTENCY_OR_MESSAGE_CONFLICT")
                verified = self.journal.verify(run_id)
                if not verified.get("ok"):
                    raise AtomicDispatchError("JOURNAL_INTEGRITY_FAILURE")
                event = self.db.execute(
                    "SELECT id,run_id,step_id,request_id,kind,epoch,payload_json "
                    "FROM events WHERE id=?", (row[7],)).fetchone()
                if event != (row[7],run_id,step_id,request_id,"INTENT",epoch,
                             canonical(payload).decode("utf-8")):
                    raise AtomicDispatchError("INTENT_LINK_DIVERGED")
                self.db.execute("COMMIT")
                return {"ok":True,"message_id":message_id,"event_id":row[7],
                        "phase":row[8],"idempotent_replay":True,
                        "transport_invoked_by_kernel":False,
                        "external_effect_status":("UNKNOWN" if row[8]=="SEND_UNCERTAIN"
                                                  else "NOT_SENT_BY_KERNEL"),
                        "identity_verified":False}
            count = self.db.execute("SELECT count(*) FROM atomic_outbox").fetchone()[0]
            if count>=MAX_OUTBOX:
                raise AtomicDispatchError("ATOMIC_OUTBOX_CAPACITY_REACHED")
            event = self.journal._append_locked(
                run_id,step_id,request_id,"INTENT",epoch,payload=payload)
            if event["idempotent_replay"]:
                # Journal-only event exists (e.g. a manual DB modification);
                # never create a new outbox row to conceal the discrepancy.
                raise AtomicDispatchError("ORPHANED_EXISTING_INTENT")
            self.db.execute(
                "INSERT INTO atomic_outbox (message_id,run_id,step_id,"
                "request_id,destination,payload_sha,epoch,intent_event_id,phase) "
                "VALUES (?,?,?,?,?,?,?,?,'INTENT_STAGED')",
                (message_id,run_id,step_id,request_id,destination,payload_sha,
                 epoch,event["event_id"]))
            self.db.execute("COMMIT")
            return {"ok":True,"message_id":message_id,
                    "event_id":event["event_id"],"phase":"INTENT_STAGED",
                    "idempotent_replay":False,
                    "transport_invoked_by_kernel":False,
                    "external_effect_status":"NOT_SENT_BY_KERNEL",
                    "identity_verified":False}
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def mark_send_uncertain(self, message_id: str, attempt_id: str,
                            epoch: int) -> dict:
        """One-way pre-send fence; never sends and never authorizes retries."""
        validate_id(message_id,"MESSAGE_ID")
        validate_id(attempt_id,"ATTEMPT_ID")
        _bounded_epoch(epoch)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute(
                "SELECT run_id,phase,epoch FROM atomic_outbox "
                "WHERE message_id=?", (message_id,)).fetchone()
            if not row:
                raise AtomicDispatchError("ATOMIC_MESSAGE_NOT_FOUND")
            if row[1]!="INTENT_STAGED" or row[2]!=epoch:
                raise AtomicDispatchError("SEND_FENCED_OR_UNCERTAIN")
            if not self.audit_run(row[0])["ok"]:
                raise AtomicDispatchError("ATOMIC_AUDIT_BLOCKED")
            self.db.execute(
                "UPDATE atomic_outbox SET phase='SEND_UNCERTAIN',"
                "send_attempt_id=? WHERE message_id=?",
                (attempt_id,message_id))
            self.db.execute("COMMIT")
            return {"ok":True,"message_id":message_id,"phase":"SEND_UNCERTAIN",
                    "attempt_id":attempt_id,"transport_invoked_by_kernel":False,
                    "external_effect_status":"UNKNOWN_AFTER_PRE_SEND_FENCE",
                    "automatic_retry_permitted":False}
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def audit_run(self, run_id: str, limit: int = MAX_INSPECT) -> dict:
        """Check bidirectional intent links, bounded, read-only."""
        validate_id(run_id,"RUN_ID")
        if type(limit) is not int or not 1<=limit<=MAX_INSPECT:
            raise AtomicDispatchError("ATOMIC_AUDIT_LIMIT_INVALID")
        verified=self.journal.verify(run_id)
        if not verified.get("ok"):
            return {"ok":False,"error":"JOURNAL_INVALID",
                    "reason":verified.get("error","UNKNOWN"),
                    "authenticated":False}
        rows=self.db.execute(
            "SELECT message_id,step_id,request_id,destination,payload_sha,"
            "epoch,intent_event_id,phase,send_attempt_id "
            "FROM atomic_outbox WHERE run_id=? ORDER BY message_id LIMIT ?",
            (run_id,limit+1)).fetchall()
        if len(rows)>limit:
            return {"ok":False,"error":"ATOMIC_AUDIT_LIMIT_EXCEEDED",
                    "authenticated":False}
        events=self.db.execute(
            "SELECT id,step_id,request_id,epoch,payload_json "
            "FROM events WHERE run_id=? AND kind='INTENT'",
            (run_id,)).fetchall()
        mapped=set()
        findings=[]
        for msg,step,req,destination,digest,epoch,event_id,phase,attempt in rows:
            payload={"message_id":msg,"destination":destination,
                     "payload_sha":digest,"atomic_dispatch":1}
            event=self.db.execute(
                "SELECT id,run_id,step_id,request_id,kind,epoch,payload_json "
                "FROM events WHERE id=?", (event_id,)).fetchone()
            if event!=(event_id,run_id,step,req,"INTENT",epoch,
                      canonical(payload).decode("utf-8")):
                findings.append(msg+":INVALID_EVENT_LINK")
            if (phase=="SEND_UNCERTAIN" and not attempt) or (
                phase=="INTENT_STAGED" and attempt is not None):
                findings.append(msg+":INVALID_SEND_PHASE")
            mapped.add(event_id)
        for event_id,step,req,epoch,raw in events:
            try:
                decoded=json.loads(raw)
            except (TypeError,ValueError):
                findings.append("INVALID_JOURNAL_PAYLOAD")
                continue
            if decoded.get("atomic_dispatch")==1 and event_id not in mapped:
                findings.append("ORPHAN_ATOMIC_INTENT")
        return {"ok":not findings,"run_id":run_id,
                "messages":len(rows),"findings":findings[:25],
                "findings_truncated":len(findings)>25,
                "uncertain_effects":sum(row[7]=="SEND_UNCERTAIN" for row in rows),
                "external_effects_verified":False,
                "authenticated":False,"can_publish":False}
