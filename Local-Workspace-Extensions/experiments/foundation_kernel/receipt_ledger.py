"""Durable anti-replay ledger for independently verified Ed25519 receipts.

Stand-alone fixture, NOT connected to agent_team.py. The signature and
content-addressed evidence are verified BEFORE SQLite admission. Local key
pinning does not establish MCP actor identity, source provenance, host grants
or proof of externally performed work.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from .signed_receipts import TrustStore, verify_signed_receipt
    from .transition import canonical, validate_id
except ImportError:
    from signed_receipts import TrustStore, verify_signed_receipt
    from transition import canonical, validate_id

DB_FILENAME = "verified_receipt_ledger_v1.sqlite3"
PROTOCOL = "signed-receipt-ledger-v1"
MAX_RECEIPTS = 10000

class ReceiptLedgerError(ValueError):
    pass

class ReceiptLedger:
    def __init__(self, root: str | Path):
        base = Path(root)
        if base.is_symlink() or getattr(base, "is_junction", lambda: False)():
            raise ReceiptLedgerError("LEDGER_ROOT_LINK_DENIED")
        try:
            base = base.resolve(strict=True)
        except OSError as exc:
            raise ReceiptLedgerError("LEDGER_ROOT_MISSING") from exc
        if not base.is_dir():
            raise ReceiptLedgerError("LEDGER_ROOT_INVALID")
        file = base / DB_FILENAME
        if file.is_symlink() or getattr(file,"is_junction",lambda:False)():
            raise ReceiptLedgerError("LEDGER_FILE_LINK_DENIED")
        if file.exists() and not file.is_file():
            raise ReceiptLedgerError("LEDGER_PATH_INVALID")
        self.db = sqlite3.connect(file, isolation_level=None, timeout=5.0)
        try:
            self.db.execute("PRAGMA busy_timeout=5000")
            self._validate_existing()
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS receipt_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipt_claims (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    envelope_sha TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    evidence_sha TEXT NOT NULL,
                    UNIQUE(run_id, step_id, kind, epoch));
            """)
            if not self.db.execute(
                "SELECT value FROM receipt_meta WHERE key='protocol'").fetchone():
                self.db.execute(
                    "INSERT INTO receipt_meta(key,value) VALUES('protocol',?)",
                    (PROTOCOL,))
        except Exception:
            self.db.close()
            raise

    def _validate_existing(self):
        tables = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return
        if not {"receipt_meta", "receipt_claims"}.issubset(tables):
            raise ReceiptLedgerError("EXISTING_UNRECOGNIZED_LEDGER")
        try:
            row=self.db.execute(
                "SELECT value FROM receipt_meta WHERE key='protocol'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ReceiptLedgerError("EXISTING_LEDGER_SCHEMA_INVALID") from exc
        if row!=(PROTOCOL,):
            raise ReceiptLedgerError("EXISTING_LEDGER_PROTOCOL_MISMATCH")

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()

    def admit(self, receipt: dict, trust: TrustStore, evidence_store, *,
              now: datetime, run_id: str, step_id: str, request_id: str,
              source_sha: str, input_sha: str, evidence_sha: str,
              kind: str, epoch: int, scope: str) -> dict:
        verified=verify_signed_receipt(
            receipt,trust,now=now,run_id=run_id,step_id=step_id,
            request_id=request_id,source_sha=source_sha,
            input_sha=input_sha,evidence_sha=evidence_sha,
            kind=kind,epoch=epoch,scope=scope)
        # EvidenceStore verifies local bytes, not actual execution/ownership.
        if evidence_store is None or not callable(getattr(evidence_store,"verify",None)):
            raise ReceiptLedgerError("EVIDENCE_STORE_REQUIRED")
        evidence=evidence_store.verify(evidence_sha)
        if not evidence.get("ok") or evidence.get("source_sha")!=source_sha:
            raise ReceiptLedgerError("EVIDENCE_LOCAL_BINDING_INVALID")
        doc=receipt["document"]
        receipt_id=doc["receipt_id"]
        signed_sha=hashlib.sha256(canonical(receipt)).hexdigest()
        claimed=(run_id,step_id,kind,epoch,signed_sha,
                 verified["verified_key_id"],evidence_sha)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing=self.db.execute(
                "SELECT run_id,step_id,kind,epoch,envelope_sha,key_id,evidence_sha "
                "FROM receipt_claims WHERE receipt_id=?", (receipt_id,)).fetchone()
            if existing:
                if existing!=claimed:
                    raise ReceiptLedgerError("RECEIPT_ID_REPLAY_CONFLICT")
                self.db.execute("COMMIT")
                return {"ok":True,"receipt_id":receipt_id,
                        "idempotent_replay":True,
                        "local_evidence_verified":True,
                        "signature_verified":True,
                        "host_identity_authenticated":False,
                        "external_effect_verified":False,
                        "can_accept_or_deploy":False}
            other=self.db.execute(
                "SELECT receipt_id FROM receipt_claims WHERE run_id=? AND "
                "step_id=? AND kind=? AND epoch=?",
                (run_id,step_id,kind,epoch)).fetchone()
            if other:
                raise ReceiptLedgerError("RECEIPT_OPERATION_ALREADY_CLAIMED")
            count=self.db.execute(
                "SELECT count(*) FROM receipt_claims").fetchone()[0]
            if count >= MAX_RECEIPTS:
                raise ReceiptLedgerError("RECEIPT_LEDGER_CAPACITY_REACHED")
            self.db.execute(
                "INSERT INTO receipt_claims(receipt_id,run_id,step_id,kind,"
                "epoch,envelope_sha,key_id,evidence_sha) VALUES(?,?,?,?,?,?,?,?)",
                (receipt_id,*claimed))
            self.db.execute("COMMIT")
            return {"ok":True,"receipt_id":receipt_id,
                    "idempotent_replay":False,
                    "local_evidence_verified":True,
                    "signature_verified":True,
                    "host_identity_authenticated":False,
                    "external_effect_verified":False,
                    "can_accept_or_deploy":False}
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def summary(self, limit:int = 50) -> dict:
        if type(limit) is not int or not 1<=limit<=100:
            raise ReceiptLedgerError("SUMMARY_LIMIT_INVALID")
        rows=self.db.execute(
            "SELECT receipt_id,run_id,step_id,kind,epoch FROM receipt_claims "
            "ORDER BY rowid DESC LIMIT ?",(limit+1,)).fetchall()
        return {"ok":True,"receipts":[
                    {"receipt_id":r[0],"run_id":r[1],"step_id":r[2],
                     "kind":r[3],"epoch":r[4]}
                    for r in rows[:limit]],
                "truncated":len(rows)>limit,
                "signature_provenance_authenticated":False,
                "host_permission_enforced":False,
                "approved_receipts":0}
