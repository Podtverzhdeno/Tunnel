"""SQLite-backed, deterministic execution *evidence* journal, isolated from live queues.

A crash after INTENT is ambiguous; NEVER automatically repeat that effect.
Every write uses BEGIN IMMEDIATE, per-run hash chaining and a unique request
ID. Hashes detect accidental/local modification but are NOT signatures.
This does NOT authenticate agents, approve work or execute a side effect.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    from .transition import (GENESIS, ProtocolError, Step, canonical, chain_hash,
                             next_step, recovery_action, validate_digest,
                             validate_id, validate_payload)
except ImportError:
    from transition import (GENESIS, ProtocolError, Step, canonical, chain_hash,
                            next_step, recovery_action, validate_digest,
                            validate_id, validate_payload)

JOURNAL_NAME = re.compile(r"^execution_journal_[a-z0-9_-]{1,40}\.sqlite3$")
SCHEMA_TAG = "local-agent-execution-journal-v1"

class JournalError(ValueError):
    pass

class Journal:
    def __init__(self, root: str | Path, name: str,
                 clock: Callable[[], datetime] | None = None):
        base = Path(root)
        if base.is_symlink() or getattr(base, "is_junction", lambda: False)():
            raise JournalError("JOURNAL_ROOT_LINK_DENIED")
        base = base.resolve(strict=True)
        if not base.is_dir():
            raise JournalError("JOURNAL_ROOT_NOT_DIRECTORY")
        if not isinstance(name, str) or not JOURNAL_NAME.fullmatch(name):
            raise JournalError("JOURNAL_NAME_INVALID")
        path = base / name
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise JournalError("JOURNAL_FILE_LINK_DENIED")
        if path.exists() and not path.is_file():
            raise JournalError("JOURNAL_PATH_NOT_FILE")
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        try:
            self._validate_existing()
        except Exception:
            self.db.close()
            raise
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            self._initialize()
        except Exception:
            self.db.close()
            raise

    def _validate_existing(self):
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return
        if "journal_meta" not in tables:
            raise JournalError("EXISTING_UNRELATED_DATABASE")
        try:
            existing = self.db.execute(
                "SELECT value FROM journal_meta WHERE key='protocol'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise JournalError("JOURNAL_PROTOCOL_INVALID") from exc
        if not existing or existing[0] != SCHEMA_TAG:
            raise JournalError("JOURNAL_PROTOCOL_MISMATCH")
        if not {"events", "heads", "steps"}.issubset(tables):
            raise JournalError("JOURNAL_TABLES_INCOMPLETE")

    def _initialize(self):
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS journal_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                evidence_sha TEXT,
                payload_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                prev_sha TEXT NOT NULL,
                entry_sha TEXT NOT NULL,
                UNIQUE(run_id, request_id));
            CREATE INDEX IF NOT EXISTS event_run_id ON events(run_id, id);
            CREATE TABLE IF NOT EXISTS heads (
                run_id TEXT PRIMARY KEY,
                event_id INTEGER NOT NULL,
                entry_sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                state TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                PRIMARY KEY(run_id, step_id));
        """)
        existing = self.db.execute(
            "SELECT value FROM journal_meta WHERE key='protocol'").fetchone()
        if existing and existing[0] != SCHEMA_TAG:
            raise JournalError("JOURNAL_PROTOCOL_MISMATCH")
        if not existing:
            if tables:
                raise JournalError("UNMARKED_JOURNAL_DENIED")
            self.db.execute(
                "INSERT INTO journal_meta (key, value) VALUES ('protocol', ?)",
                (SCHEMA_TAG,))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def append(self, run_id: str, step_id: str, request_id: str,
               kind: str, epoch: int, evidence_sha: str | None = None,
               payload: dict | None = None) -> dict:
        """Single-event transaction. No external side effects."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = self._append_locked(run_id, step_id, request_id, kind,
                                         epoch, evidence_sha, payload)
            self.db.execute("COMMIT")
            return result
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _append_locked(self, run_id: str, step_id: str, request_id: str,
                       kind: str, epoch: int, evidence_sha: str | None = None,
                       payload: dict | None = None) -> dict:
        """Internal event mutation; caller owns the SQLite transaction.

        Required by atomic outbox staging; NEVER call without BEGIN IMMEDIATE.
        No commit happens here, so extra rows may be inserted before COMMIT.
        """
        for key, value in (("RUN_ID", run_id), ("STEP_ID", step_id),
                           ("REQUEST_ID", request_id)):
            validate_id(value, key)
        validate_payload({} if payload is None else payload)
        payload_json = canonical({} if payload is None else payload).decode("utf-8")
        validate_digest(evidence_sha)
        if kind not in {"PLAN", "LEASE", "RELEASE", "INTENT", "RESULT",
                        "RECONCILIATION", "RECONCILED_RESULT", "QA_OBSERVED",
                        "CANCEL"}:
            raise ProtocolError("EVENT_KIND_DENIED")
        if type(epoch) is not int or not 0 <= epoch <= 2**31-1:
            raise ProtocolError("EPOCH_INVALID")
        if not self.db.in_transaction:
            raise JournalError("JOURNAL_TRANSACTION_REQUIRED")
        try:
            # No new state or even idempotent response from a corrupt history.
            # Bounded full replay favors correctness over throughput in this
            # isolated reference kernel (limit: 10,000 events per run).
            has_events = self.db.execute(
                "SELECT 1 FROM events WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone()
            if has_events and not self.verify(run_id).get("ok"):
                raise JournalError("EXISTING_JOURNAL_DIVERGED")
            prior = self.db.execute(
                "SELECT id, step_id, kind, epoch, evidence_sha, payload_json, entry_sha "
                "FROM events WHERE run_id=? AND request_id=?",
                (run_id, request_id)).fetchone()
            if prior:
                if (prior[1], prior[2], prior[3], prior[4], prior[5]) != (
                        step_id, kind, epoch, evidence_sha, payload_json):
                    raise JournalError("IDEMPOTENCY_CONFLICT")
                return {"ok": True, "event_id": prior[0], "entry_sha": prior[6],
                        "idempotent_replay": True}
            cached = self.db.execute(
                "SELECT state, epoch FROM steps WHERE run_id=? AND step_id=?",
                (run_id, step_id)).fetchone()
            next_state = next_step(
                Step(*cached) if cached else Step(), kind, epoch, evidence_sha)
            head = self.db.execute(
                "SELECT event_id, entry_sha FROM heads WHERE run_id=?",
                (run_id,)).fetchone()
            prev = head[1] if head else GENESIS
            if head:
                last = self.db.execute("SELECT entry_sha FROM events WHERE id=?",
                                       (head[0],)).fetchone()
                if not last or last[0] != prev:
                    raise JournalError("CHAIN_HEAD_DIVERGED")
            now = self.clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise JournalError("CLOCK_INVALID")
            created = now.astimezone(timezone.utc).isoformat()
            envelope = {
                "run_id":run_id, "step_id":step_id, "request_id":request_id,
                "kind":kind, "epoch":epoch, "evidence_sha":evidence_sha,
                "payload":json.loads(payload_json), "created_utc":created,
                "prev_sha":prev,
            }
            entry_sha = chain_hash(envelope)
            cursor = self.db.execute(
                "INSERT INTO events (run_id, step_id, request_id, kind, epoch, "
                "evidence_sha, payload_json, created_utc, prev_sha, entry_sha) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, step_id, request_id, kind, epoch, evidence_sha,
                 payload_json, created, prev, entry_sha))
            event_id = cursor.lastrowid
            self.db.execute(
                "INSERT INTO heads (run_id, event_id, entry_sha) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET event_id=excluded.event_id, "
                "entry_sha=excluded.entry_sha",
                (run_id, event_id, entry_sha))
            self.db.execute(
                "INSERT INTO steps (run_id, step_id, state, epoch) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, step_id) DO UPDATE SET state=excluded.state, "
                "epoch=excluded.epoch",
                (run_id, step_id, next_state.state, next_state.epoch))
            return {"ok": True, "event_id": event_id, "entry_sha": entry_sha,
                    "state": next_state.state, "idempotent_replay": False}
        except Exception:
            # The caller (append or the atomic dispatcher) performs rollback.
            raise

    def verify(self, run_id: str, max_events: int = 10000) -> dict:
        """Replay and compare all state snapshots. Read-only, fail-closed."""
        validate_id(run_id, "RUN_ID")
        if type(max_events) is not int or not 1 <= max_events <= 10000:
            raise JournalError("VERIFY_LIMIT_INVALID")
        rows = self.db.execute(
            "SELECT id, step_id, request_id, kind, epoch, evidence_sha, "
            "payload_json, created_utc, prev_sha, entry_sha "
            "FROM events WHERE run_id=? ORDER BY id LIMIT ?", (run_id, max_events+1)
        ).fetchall()
        if len(rows) > max_events:
            return {"ok": False, "error": "VERIFY_LIMIT_EXCEEDED",
                    "events_examined": max_events}
        if not rows:
            return {"ok": False, "error": "RUN_NOT_FOUND", "events_examined":0}
        head = GENESIS
        states: dict[str,Step] = {}
        try:
            for row in rows:
                ident, step_id, request_id, kind, epoch, evidence, payload_s, utc, prev, stored = row
                if prev != head:
                    raise JournalError("CHAIN_LINK_DIVERGED")
                payload = json.loads(payload_s)
                validate_payload(payload)
                if canonical(payload).decode("utf-8") != payload_s:
                    raise JournalError("PAYLOAD_SERIALIZATION_DIVERGED")
                envelope = dict(run_id=run_id, step_id=step_id,
                                request_id=request_id, kind=kind, epoch=epoch,
                                evidence_sha=evidence, payload=payload,
                                created_utc=utc, prev_sha=prev)
                if chain_hash(envelope) != stored:
                    raise JournalError("EVENT_HASH_DIVERGED")
                states[step_id] = next_step(
                    states.get(step_id, Step()), kind, epoch, evidence)
                head = stored
            latest = self.db.execute(
                "SELECT event_id, entry_sha FROM heads WHERE run_id=?", (run_id,)
            ).fetchone()
            if latest != (rows[-1][0], head):
                raise JournalError("CHAIN_HEAD_DIVERGED")
            cached = {step_id: Step(state, epoch) for step_id, state, epoch in
                      self.db.execute("SELECT step_id, state, epoch FROM steps WHERE run_id=?",
                                      (run_id,))}
            if states != cached:
                raise JournalError("STEP_STATE_CACHE_DIVERGED")
        except (JournalError, ProtocolError, ValueError, TypeError,
                KeyError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)[:120],
                    "events_examined": len(rows)}
        return {"ok": True, "events_examined": len(rows),
                "head_sha": head,
                "steps": {k: {"state":v.state, "epoch":v.epoch,
                              "safe_next_action":recovery_action(v)}
                          for k,v in sorted(states.items())},
                "identity_and_evidence_authenticated": False,
                "external_effects_executed": False}

    def recovery(self, run_id: str) -> dict:
        """Never schedules retries: suggest only verified next step categories."""
        report = self.verify(run_id)
        if not report["ok"]:
            return {"ok": False, "action": "MANUAL_INSPECTION",
                    "reason": report["error"]}
        return {"ok": True, "steps": report["steps"],
                "automatic_external_retry_permitted": False}
