"""Durable local outbox/inbox reference protocol; no transport or effect executor.

At-least-once *intent* can be recorded, NOT exactly-once external effects.
A claimed send or begun processing is uncertain until an external trusted
reconciler provides proof. This module does not authenticate proof or agents.
Never connect it to the real five-agent coordinator without approved design.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable

try:
    from .transition import ProtocolError, canonical, validate_id, validate_digest
except ImportError:
    from transition import ProtocolError, canonical, validate_id, validate_digest

PROTOCOL = "agent-durable-mailbox-v1"
FILENAME = "execution_mailbox_v1.sqlite3"
REQUIRED_TABLES = frozenset({"mailbox_meta", "outbox", "inbox", "requests"})
MAX_MESSAGES = 1000
MAX_MAILBOX_ROWS = 10000
MAX_REQUEST_ROWS = 50000
MAX_EPOCH = 2**31 - 1

class MailboxError(ValueError):
    pass

def _integer(value, label: str, *, minimum: int = 1) -> None:
    if type(value) is not int or not minimum <= value <= MAX_EPOCH:
        raise MailboxError(label + "_INVALID")

def _hash_request(command: str, args: dict) -> str:
    return hashlib.sha256(canonical([command, args])).hexdigest()

class DurableMailbox:
    """Transactionally persist message intents; no network, shell or scheduler."""

    def __init__(self, root: str | Path):
        path = Path(root)
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise MailboxError("MAILBOX_ROOT_LINK_DENIED")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise MailboxError("MAILBOX_ROOT_INVALID")
        file = path / FILENAME
        if file.is_symlink() or getattr(file, "is_junction", lambda: False)():
            raise MailboxError("MAILBOX_FILE_LINK_DENIED")
        if file.exists() and not file.is_file():
            raise MailboxError("MAILBOX_PATH_INVALID")
        self.path = file
        self.db = sqlite3.connect(str(file), timeout=5.0, isolation_level=None)
        try:
            self.db.execute("PRAGMA busy_timeout=5000")
            self._check_schema()
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self._init_schema()
        except Exception:
            self.db.close()
            raise

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self) -> None: self.db.close()

    def _check_schema(self) -> None:
        tables = {x[0] for x in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return
        if not REQUIRED_TABLES.issubset(tables):
            raise MailboxError("MAILBOX_UNRECOGNIZED_DATABASE")
        try:
            result = self.db.execute(
                "SELECT value FROM mailbox_meta WHERE key='protocol'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise MailboxError("MAILBOX_SCHEMA_INVALID") from exc
        if result != (PROTOCOL,):
            raise MailboxError("MAILBOX_PROTOCOL_MISMATCH")

    def _init_schema(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mailbox_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                message_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                step_id TEXT NOT NULL, destination TEXT NOT NULL,
                payload_sha TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN
                    ('STAGED','SEND_UNCERTAIN','ACK_REPORTED')),
                epoch INTEGER NOT NULL, attempt_id TEXT,
                ack_sha TEXT
            );
            CREATE TABLE IF NOT EXISTS inbox (
                consumer TEXT NOT NULL, message_id TEXT NOT NULL,
                payload_sha TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN
                    ('RECEIVED','PROCESS_UNCERTAIN','RESULT_REPORTED')),
                epoch INTEGER NOT NULL, attempt_id TEXT,
                receipt_sha TEXT,
                PRIMARY KEY(consumer,message_id)
            );
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, command TEXT NOT NULL,
                arguments_sha TEXT NOT NULL, result_json TEXT NOT NULL
            );
        """)
        if not self.db.execute(
            "SELECT value FROM mailbox_meta WHERE key='protocol'").fetchone():
            self.db.execute(
                "INSERT INTO mailbox_meta(key,value) VALUES('protocol',?)",
                (PROTOCOL,))

    def _change(self, request_id: str, command: str, args: dict,
                action: Callable[[], dict]) -> dict:
        validate_id(request_id, "REQUEST_ID")
        digest = _hash_request(command, args)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT command,arguments_sha,result_json FROM requests "
                "WHERE request_id=?", (request_id,)).fetchone()
            if previous is not None:
                if previous[:2] != (command, digest):
                    raise MailboxError("REQUEST_IDEMPOTENCY_CONFLICT")
                result = json.loads(previous[2])
                self.db.execute("COMMIT")
                return {**result, "idempotent_replay": True}
            if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= MAX_REQUEST_ROWS:
                raise MailboxError("REQUEST_HISTORY_CAPACITY_REACHED")
            result = action()
            result = {**result, "idempotent_replay": False,
                      "authority_verified": False}
            self.db.execute(
                "INSERT INTO requests(request_id,command,arguments_sha,result_json) "
                "VALUES(?,?,?,?)",
                (request_id, command, digest, canonical(result).decode("utf-8")))
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def stage(self, request_id: str, message_id: str, run_id: str,
              step_id: str, destination: str, payload_sha: str) -> dict:
        for label,value in (("MESSAGE_ID",message_id),("RUN_ID",run_id),
                            ("STEP_ID",step_id),("DESTINATION",destination)):
            validate_id(value,label)
        validate_digest(payload_sha,required=True)
        args = dict(message_id=message_id,run_id=run_id,step_id=step_id,
                    destination=destination,payload_sha=payload_sha)
        def action():
            row=self.db.execute(
                "SELECT run_id,step_id,destination,payload_sha,phase,epoch "
                "FROM outbox WHERE message_id=?", (message_id,)).fetchone()
            if row:
                if row[:4] != (run_id,step_id,destination,payload_sha):
                    raise MailboxError("MESSAGE_ID_CONFLICT")
                return {"ok":True,"message_id":message_id,"phase":row[4],
                        "epoch":row[5],"already_staged":True}
            if self.db.execute("SELECT count(*) FROM outbox").fetchone()[0] >= MAX_MAILBOX_ROWS:
                raise MailboxError("OUTBOX_CAPACITY_REACHED")
            self.db.execute(
                "INSERT INTO outbox(message_id,run_id,step_id,destination,"
                "payload_sha,phase,epoch) VALUES(?,?,?,?,?,'STAGED',0)",
                (message_id,run_id,step_id,destination,payload_sha))
            return {"ok":True,"message_id":message_id,"phase":"STAGED",
                    "epoch":0,"already_staged":False}
        return self._change(request_id, "stage", args, action)

    def begin_send(self, request_id: str, message_id: str,
                   attempt_id: str, epoch: int) -> dict:
        validate_id(message_id, "MESSAGE_ID")
        validate_id(attempt_id, "ATTEMPT_ID")
        _integer(epoch, "EPOCH")
        args=dict(message_id=message_id,attempt_id=attempt_id,epoch=epoch)
        def action():
            row=self.db.execute(
                "SELECT phase,epoch,payload_sha,destination FROM outbox "
                "WHERE message_id=?", (message_id,)).fetchone()
            if not row:
                raise MailboxError("OUTBOX_MESSAGE_MISSING")
            if row[0] != "STAGED" or epoch <= row[1]:
                raise MailboxError("SEND_REQUIRES_MANUAL_RECONCILIATION")
            self.db.execute(
                "UPDATE outbox SET phase='SEND_UNCERTAIN',epoch=?,attempt_id=? "
                "WHERE message_id=?", (epoch,attempt_id,message_id))
            return {"ok":True,"message_id":message_id,"phase":"SEND_UNCERTAIN",
                    "epoch":epoch,"payload_sha":row[2],"destination":row[3],
                    "transport_called":False,"automatic_retry_permitted":False}
        return self._change(request_id, "begin_send", args, action)

    def report_ack(self, request_id: str, message_id: str, epoch: int,
                   ack_sha: str) -> dict:
        validate_id(message_id,"MESSAGE_ID")
        _integer(epoch,"EPOCH")
        validate_digest(ack_sha,required=True)
        args=dict(message_id=message_id,epoch=epoch,ack_sha=ack_sha)
        def action():
            row=self.db.execute(
                "SELECT phase,epoch FROM outbox WHERE message_id=?",
                (message_id,)).fetchone()
            if not row or row != ("SEND_UNCERTAIN",epoch):
                raise MailboxError("ACK_ATTEMPT_FENCED_OR_INVALID")
            self.db.execute(
                "UPDATE outbox SET phase='ACK_REPORTED',ack_sha=? "
                "WHERE message_id=?", (ack_sha,message_id))
            return {"ok":True,"message_id":message_id,"phase":"ACK_REPORTED",
                    "ack_authenticated":False,"delivery_verified":False}
        return self._change(request_id, "report_ack", args, action)

    def receive(self, request_id: str, consumer: str, message_id: str,
                payload_sha: str) -> dict:
        for label,value in (("CONSUMER",consumer),("MESSAGE_ID",message_id)):
            validate_id(value,label)
        validate_digest(payload_sha,required=True)
        args=dict(consumer=consumer,message_id=message_id,payload_sha=payload_sha)
        def action():
            row=self.db.execute(
                "SELECT payload_sha,phase,epoch FROM inbox "
                "WHERE consumer=? AND message_id=?", (consumer,message_id)).fetchone()
            if row:
                if row[0] != payload_sha:
                    raise MailboxError("INBOX_DUPLICATE_PAYLOAD_CONFLICT")
                return {"ok":True,"message_id":message_id,"phase":row[1],
                        "epoch":row[2],"duplicate_delivery":True,
                        "process_again_permitted":False}
            if self.db.execute("SELECT count(*) FROM inbox").fetchone()[0] >= MAX_MAILBOX_ROWS:
                raise MailboxError("INBOX_CAPACITY_REACHED")
            self.db.execute(
                "INSERT INTO inbox(consumer,message_id,payload_sha,phase,epoch) "
                "VALUES(?,?,?,'RECEIVED',0)",
                (consumer,message_id,payload_sha))
            return {"ok":True,"message_id":message_id,"phase":"RECEIVED",
                    "epoch":0,"duplicate_delivery":False}
        return self._change(request_id, "receive", args, action)

    def begin_processing(self, request_id: str, consumer: str, message_id: str,
                         attempt_id: str, epoch: int) -> dict:
        for label,value in (("CONSUMER",consumer),("MESSAGE_ID",message_id),
                            ("ATTEMPT_ID",attempt_id)):
            validate_id(value,label)
        _integer(epoch,"EPOCH")
        args=dict(consumer=consumer,message_id=message_id,
                  attempt_id=attempt_id,epoch=epoch)
        def action():
            row=self.db.execute(
                "SELECT phase,epoch,payload_sha FROM inbox "
                "WHERE consumer=? AND message_id=?", (consumer,message_id)).fetchone()
            if not row:
                raise MailboxError("INBOX_MESSAGE_MISSING")
            if row[0] != "RECEIVED" or epoch <= row[1]:
                raise MailboxError("PROCESS_REQUIRES_MANUAL_RECONCILIATION")
            self.db.execute(
                "UPDATE inbox SET phase='PROCESS_UNCERTAIN',epoch=?,attempt_id=? "
                "WHERE consumer=? AND message_id=?",
                (epoch,attempt_id,consumer,message_id))
            return {"ok":True,"message_id":message_id,"phase":"PROCESS_UNCERTAIN",
                    "epoch":epoch,"payload_sha":row[2],
                    "worker_executed":False,"automatic_retry_permitted":False}
        return self._change(request_id, "begin_processing", args, action)

    def report_result(self, request_id: str, consumer: str, message_id: str,
                      epoch: int, receipt_sha: str) -> dict:
        for label,value in (("CONSUMER",consumer),("MESSAGE_ID",message_id)):
            validate_id(value,label)
        _integer(epoch,"EPOCH")
        validate_digest(receipt_sha,required=True)
        args=dict(consumer=consumer,message_id=message_id,
                  epoch=epoch,receipt_sha=receipt_sha)
        def action():
            row=self.db.execute(
                "SELECT phase,epoch FROM inbox "
                "WHERE consumer=? AND message_id=?", (consumer,message_id)).fetchone()
            if row != ("PROCESS_UNCERTAIN",epoch):
                raise MailboxError("RESULT_ATTEMPT_FENCED_OR_INVALID")
            self.db.execute(
                "UPDATE inbox SET phase='RESULT_REPORTED',receipt_sha=? "
                "WHERE consumer=? AND message_id=?",
                (receipt_sha,consumer,message_id))
            return {"ok":True,"message_id":message_id,
                    "phase":"RESULT_REPORTED","receipt_authenticated":False,
                    "effect_verified":False}
        return self._change(request_id, "report_result", args, action)

    def snapshot(self, limit: int = 200) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
            raise MailboxError("SNAPSHOT_LIMIT_INVALID")
        out=self.db.execute(
            "SELECT message_id,phase,epoch FROM outbox ORDER BY message_id "
            "LIMIT ?", (limit+1,)).fetchall()
        inc=self.db.execute(
            "SELECT consumer,message_id,phase,epoch FROM inbox "
            "ORDER BY consumer,message_id LIMIT ?", (limit+1,)).fetchall()
        return {"ok":True, "outbox":[
            {"message_id":r[0],"phase":r[1],"epoch":r[2],
             "safe_next_action":("PREPARE_INTENT_WITH_TRUSTED_DISPATCHER" if r[1]=="STAGED"
                                  else "RECONCILE_OR_VERIFY_EXTERNAL_ACK")}
            for r in out[:limit]],
            "inbox":[
            {"consumer":r[0],"message_id":r[1],"phase":r[2],"epoch":r[3],
             "safe_next_action":("PREPARE_EFFECT_WITH_TRUSTED_EXECUTOR" if r[2]=="RECEIVED"
                                  else "RECONCILE_OR_VERIFY_EXTERNAL_RESULT")}
            for r in inc[:limit]],
            "truncated":len(out)>limit or len(inc)>limit,
            "sender_receiver_atomicity":False,
            "external_effects_executed":False,
            "authenticated_actor_or_ack":False}
