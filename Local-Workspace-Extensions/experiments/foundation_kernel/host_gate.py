"""Authenticated, atomic, deny-by-default dispatch *reference boundary*.

Verifies Ed25519 command + provisioned capability, then commits an anti-replay
record AND journal INTENT/outbox transition in ONE SQLite transaction.
Only two explicitly named operations are exposed. No generic execute(),
no shell, Git, approval/deployment, transport or MCP integration.

This is a lab boundary, not the production host's access-control layer:
direct access to AtomicDispatch/Journal still bypasses it in the experiment.
"""
from __future__ import annotations
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

try:
    from .atomic_dispatch import AtomicDispatch
    from .host_auth import HostPolicy, verify_host_command
    from .transition import canonical, validate_id
except ImportError:
    from atomic_dispatch import AtomicDispatch
    from host_auth import HostPolicy, verify_host_command
    from transition import canonical, validate_id

SCHEMA = "signed-host-dispatch-gate-v1"
MAX_CLAIMS = 20000

class HostGateError(ValueError):
    pass

class HostDispatchGate:
    def __init__(self, dispatch: AtomicDispatch, policy: HostPolicy):
        if type(dispatch) is not AtomicDispatch or type(policy) is not HostPolicy:
            raise HostGateError("HOST_GATE_COMPONENT_INVALID")
        self.dispatch = dispatch
        self.db = dispatch.db
        self.policy = policy
        # Signed-policy sessions set this pinned floor after issuer verification.
        # Legacy offline test fixtures leave it unset.
        self.active_policy_floor = None
        self.active_policy_expiry_utc = None
        if self.db.in_transaction:
            raise HostGateError("HOST_GATE_SCHEMA_REQUIRES_IDLE_CONNECTION")
        existing = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        names = {"host_gate_meta", "host_gate_claims"}
        if existing.intersection(names):
            if not names.issubset(existing):
                raise HostGateError("HOST_GATE_INCOMPLETE_SCHEMA")
            try:
                marker = self.db.execute(
                    "SELECT value FROM host_gate_meta WHERE key='protocol'").fetchone()
            except sqlite3.DatabaseError as exc:
                raise HostGateError("HOST_GATE_BAD_SCHEMA") from exc
            if marker != (SCHEMA,):
                raise HostGateError("HOST_GATE_PROTOCOL_MISMATCH")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS host_gate_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS host_gate_claims (
                request_id TEXT PRIMARY KEY,
                key_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                action TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                document_sha TEXT NOT NULL,
                UNIQUE(key_id, nonce));
            CREATE TABLE IF NOT EXISTS host_gate_command_targets (
                request_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                message_id TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES host_gate_claims(request_id)
            );
        """)
        if not existing.intersection(names):
            self.db.execute(
                "INSERT INTO host_gate_meta(key,value) VALUES('protocol',?)",
                (SCHEMA,))

    def _invoke(self, *, envelope: dict, now: datetime,
                action: str, run_id: str, step_id: str,
                request_id: str, epoch: int, scope: str,
                arguments: dict) -> dict:
        identity = verify_host_command(
            envelope, self.policy, now=now,
            action=action, run_id=run_id, step_id=step_id,
            request_id=request_id, epoch=epoch, scope=scope,
            arguments=arguments)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.active_policy_floor is not None:
                try:
                    from .signed_policy import (
                        verify_current_floor_locked, PolicySignatureError)
                except ImportError:
                    from signed_policy import (
                        verify_current_floor_locked, PolicySignatureError)
                verify_current_floor_locked(self.db, self.active_policy_floor)
                if (not isinstance(self.active_policy_expiry_utc,datetime) or
                    self.active_policy_expiry_utc.tzinfo is None or
                    datetime.now(timezone.utc)>self.active_policy_expiry_utc):
                    raise PolicySignatureError("SIGNED_POLICY_EXPIRED_DURING_SESSION")
            if self.db.execute(
                "SELECT 1 FROM host_gate_claims WHERE request_id=? OR "
                "(key_id=? AND nonce=?) LIMIT 1",
                (request_id, identity["key_id"], identity["nonce"])).fetchone():
                raise HostGateError("HOST_REQUEST_OR_NONCE_ALREADY_CONSUMED")
            if self.db.execute(
                "SELECT count(*) FROM host_gate_claims").fetchone()[0] >= MAX_CLAIMS:
                raise HostGateError("HOST_GATE_CAPACITY_REACHED")
            if action == "STAGE_INTENT":
                result = self._stage_locked(
                    run_id, step_id, request_id, epoch, arguments)
            elif action == "MARK_SEND_UNCERTAIN":
                result = self._mark_locked(
                    run_id, step_id, epoch, arguments)
            else:
                raise HostGateError("HOST_ACTION_NOT_EXPOSED")
            document_sha = hashlib.sha256(canonical(envelope)).hexdigest()
            self.db.execute(
                "INSERT INTO host_gate_claims(request_id,key_id,nonce,"
                "principal_id,run_id,step_id,action,epoch,document_sha) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (request_id,identity["key_id"],identity["nonce"],
                 identity["principal_id"],run_id,step_id,action,epoch,
                 document_sha))
            self.db.execute(
                "INSERT INTO host_gate_command_targets(request_id,action,message_id) "
                "VALUES (?,?,?)",
                (request_id,action,arguments["message_id"]))
            self.db.execute("COMMIT")
            return {
                **result, "authenticated_principal_id":identity["principal_id"],
                "authenticated_role":identity["trusted_role"],
                "host_request_signature_verified":True,
                "host_capability_checked":True,
                "host_nonce_consumed":True,
                "local_atomic_commit":True,
                "live_mcp_host_enforced":False,
                "can_accept_review_or_deploy":False,
            }
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _stage_locked(self, run_id: str, step_id: str,
                      request_id: str, epoch: int, args: dict) -> dict:
        if not self.dispatch.audit_run(run_id).get("ok"):
            raise HostGateError("HOST_ATOMIC_AUDIT_FAILED")
        mid, destination, digest = (
            args["message_id"], args["destination"], args["payload_sha"])
        already = self.db.execute(
            "SELECT 1 FROM atomic_outbox WHERE message_id=? "
            "OR (run_id=? AND request_id=?)",
            (mid,run_id,request_id)).fetchone()
        if already:
            raise HostGateError("HOST_OUTBOX_MESSAGE_OR_REQUEST_ALREADY_EXISTS")
        import importlib
        module = importlib.import_module(AtomicDispatch.__module__)
        limit = module.MAX_OUTBOX
        if self.db.execute(
            "SELECT count(*) FROM atomic_outbox").fetchone()[0] >= limit:
            raise HostGateError("HOST_OUTBOX_CAPACITY_REACHED")
        payload = {
            "message_id":mid,"destination":destination,
            "payload_sha":digest,"atomic_dispatch":1,
        }
        entry=self.dispatch.journal._append_locked(
            run_id,step_id,request_id,"INTENT",epoch,payload=payload)
        if entry["idempotent_replay"]:
            raise HostGateError("HOST_EXISTING_ORPHAN_INTENT")
        self.db.execute(
            "INSERT INTO atomic_outbox(message_id,run_id,step_id,request_id,"
            "destination,payload_sha,epoch,intent_event_id,phase) "
            "VALUES(?,?,?,?,?,?,?,?,'INTENT_STAGED')",
            (mid,run_id,step_id,request_id,destination,digest,
             epoch,entry["event_id"]))
        return {
            "ok":True,"action":"STAGE_INTENT",
            "message_id":mid,"event_id":entry["event_id"],
            "phase":"INTENT_STAGED","transport_invoked":False,
        }

    def _mark_locked(self, run_id: str, step_id: str,
                     epoch: int, args: dict) -> dict:
        mid, attempt = args["message_id"], args["attempt_id"]
        row=self.db.execute(
            "SELECT run_id,step_id,epoch,phase FROM atomic_outbox "
            "WHERE message_id=?", (mid,)).fetchone()
        if row is None:
            raise HostGateError("HOST_MESSAGE_NOT_FOUND")
        if row != (run_id,step_id,epoch,"INTENT_STAGED"):
            raise HostGateError("HOST_SEND_STALE_OR_ALREADY_UNCERTAIN")
        if not self.dispatch.audit_run(run_id).get("ok"):
            raise HostGateError("HOST_ATOMIC_AUDIT_FAILED")
        self.db.execute(
            "UPDATE atomic_outbox SET phase='SEND_UNCERTAIN',"
            "send_attempt_id=? WHERE message_id=?",
            (attempt,mid))
        return {
            "ok":True,"action":"MARK_SEND_UNCERTAIN",
            "message_id":mid,"phase":"SEND_UNCERTAIN",
            "transport_invoked":False, "automatic_retry_permitted":False,
            "external_effect_state":"UNKNOWN",
        }

    def stage_intent(self, envelope: dict, *, now: datetime, scope: str,
                     run_id: str, step_id: str, request_id: str,
                     epoch: int, message_id: str, destination: str,
                     payload_sha: str) -> dict:
        return self._invoke(
            envelope=envelope,now=now,action="STAGE_INTENT",
            scope=scope,run_id=run_id,step_id=step_id,
            request_id=request_id,epoch=epoch,
            arguments={"message_id":message_id,"destination":destination,
                       "payload_sha":payload_sha})

    def mark_send_uncertain(self, envelope: dict, *, now: datetime,
                            scope: str, run_id: str, step_id: str,
                            request_id: str, epoch: int, message_id: str,
                            attempt_id: str) -> dict:
        return self._invoke(
            envelope=envelope,now=now,action="MARK_SEND_UNCERTAIN",
            scope=scope,run_id=run_id,step_id=step_id,
            request_id=request_id,epoch=epoch,
            arguments={"message_id":message_id,"attempt_id":attempt_id})

    def audit(self, run_id: str) -> dict:
        """Local consistency check, never permission to publish or deploy."""
        validate_id(run_id,"RUN_ID")
        report=self.dispatch.audit_run(run_id)
        return {
            **report,
            "claims_for_run": self.db.execute(
                "SELECT count(*) FROM host_gate_claims WHERE run_id=?",
                (run_id,)).fetchone()[0],
            "host_permission_enforced_in_live_mcp":False,
            "production_deployment_authorized":False,
        }
