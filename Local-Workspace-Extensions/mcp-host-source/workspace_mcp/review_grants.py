"""P085 offline host-side reviewer grant verification and replay fencing.

NOT a connected MCP tool or an authentication issuer. The caller must be a
separate OS-protected reviewer service that provides *trusted* actor/session
and live coordinator snapshot, and alone holds its HMAC verification secret.
In particular, the broad legacy MCP run_script host CANNOT use this module
as an authorization boundary while sharing its OS identity/filesystem.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{64}\Z")
NONCE = re.compile(r"[0-9a-f]{32}\Z")
TASK = re.compile(r"P[0-9]{3,6}\Z")
ACTOR = re.compile(r"[a-zA-Z0-9_.:@-]{3,128}\Z")
WORKSPACE = re.compile(r"[a-zA-Z0-9_-]{1,40}\Z")
ACTIONS = frozenset({"accept", "reject"})
ISSUER = "owner-gateway-v1"
AUDIENCE = "local-workspace-review-gateway"
SCHEMA = "p085-review-grant-v2"
FIELDS = frozenset({"schema", "issuer", "audience", "nonce", "actor_id",
                    "workspace", "task_id", "action", "revision",
                    "spec_sha256", "source_bundle_sha256", "review_lease_sha256",
                    "issued_at", "expires_at"})
MAX_GRANT_BYTES = 4096


class GrantDenied(ValueError):
    """Stable, nonsensitive error codes. Never print bearer grants/keys."""


@dataclass(frozen=True)
class ReviewTarget:
    """Factual state checked by the privileged host, NOT the MCP caller."""
    workspace: str
    task_id: str
    revision: int
    spec_sha256: str
    source_bundle_sha256: str
    review_lease_sha256: str
    review_lease_active: bool
    merge_intent_pending: bool
    author_actor_id: str


def canonical(payload: dict) -> bytes:
    if type(payload) is not dict or set(payload) != FIELDS:
        raise GrantDenied("INVALID_GRANT_FIELDS")
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise GrantDenied("INVALID_GRANT_ENCODING") from exc
    if len(raw) > MAX_GRANT_BYTES:
        raise GrantDenied("OVERSIZED_GRANT")
    return raw


def _valid(payload: dict, now: int) -> None:
    if payload["schema"] != SCHEMA or payload["issuer"] != ISSUER or payload["audience"] != AUDIENCE:
        raise GrantDenied("INVALID_AUDIENCE_OR_ISSUER")
    if (type(payload["nonce"]) is not str or not NONCE.fullmatch(payload["nonce"])
            or type(payload["actor_id"]) is not str or not ACTOR.fullmatch(payload["actor_id"])
            or type(payload["task_id"]) is not str or not TASK.fullmatch(payload["task_id"])
            or type(payload["workspace"]) is not str or not WORKSPACE.fullmatch(payload["workspace"])
            or type(payload["action"]) is not str or payload["action"] not in ACTIONS):
        raise GrantDenied("INVALID_GRANT_SCOPE")
    if (type(payload["revision"]) is not int or payload["revision"] < 0
            or payload["revision"] > 2**63 - 1
            or type(payload["spec_sha256"]) is not str or not SHA.fullmatch(payload["spec_sha256"])
            or type(payload["source_bundle_sha256"]) is not str
            or not SHA.fullmatch(payload["source_bundle_sha256"])
            or type(payload["review_lease_sha256"]) is not str
            or not SHA.fullmatch(payload["review_lease_sha256"])):
        raise GrantDenied("INVALID_GRANT_HASH_OR_REVISION")
    issued, expiry = payload["issued_at"], payload["expires_at"]
    if (type(issued) is not int or type(expiry) is not int or
            issued > now + 15 or issued < now - 300 or
            expiry <= now or expiry <= issued or expiry > issued + 300):
        raise GrantDenied("EXPIRED_OR_INVALID_GRANT_TIME")


def verify(envelope: dict, key: bytes, *, authenticated_actor: str,
           target: ReviewTarget, now: int | None = None) -> dict:
    """Verify signed scope against host-authenticated actor + actual task state.

    Actor authentication, target construction, key custody and OS isolation
    are obligations of a distinct privileged host, NOT implemented here.
    """
    moment = int(time.time()) if now is None else now
    if type(moment) is not int:
        raise GrantDenied("INVALID_CLOCK")
    if type(key) is not bytes or len(key) < 32:
        raise GrantDenied("TRUSTED_KEY_UNAVAILABLE")
    if type(authenticated_actor) is not str or not ACTOR.fullmatch(authenticated_actor):
        raise GrantDenied("AUTHENTICATED_ACTOR_MISSING")
    if type(target) is not ReviewTarget:
        raise GrantDenied("TRUSTED_TARGET_MISSING")
    # Frozen dataclass annotations are not enforced at runtime. A malformed
    # trusted-coordinator snapshot must fail CLOSED rather than relying on
    # Python truthiness or equality (e.g. 4.0 == 4, "true" is truthy).
    if (type(target.workspace) is not str or not WORKSPACE.fullmatch(target.workspace)
            or type(target.task_id) is not str or not TASK.fullmatch(target.task_id)
            or type(target.revision) is not int
            or not 0 <= target.revision <= 2**63 - 1
            or type(target.spec_sha256) is not str
            or not SHA.fullmatch(target.spec_sha256)
            or type(target.source_bundle_sha256) is not str
            or not SHA.fullmatch(target.source_bundle_sha256)
            or type(target.review_lease_sha256) is not str
            or not SHA.fullmatch(target.review_lease_sha256)
            or type(target.review_lease_active) is not bool
            or type(target.merge_intent_pending) is not bool
            or type(target.author_actor_id) is not str
            or not ACTOR.fullmatch(target.author_actor_id)):
        raise GrantDenied("TRUSTED_TARGET_INVALID")
    if type(envelope) is not dict or set(envelope) != {"payload", "mac"}:
        raise GrantDenied("INVALID_ENVELOPE")
    payload = envelope["payload"]
    raw = canonical(payload)
    mac = envelope["mac"]
    if type(mac) is not str or not SHA.fullmatch(mac):
        raise GrantDenied("INVALID_SIGNATURE")
    calculated = hmac.new(key, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, calculated):
        raise GrantDenied("INVALID_SIGNATURE")
    _valid(payload, moment)
    if payload["actor_id"] != authenticated_actor:
        raise GrantDenied("ACTOR_MISMATCH")
    if (payload["workspace"] != target.workspace or
            payload["task_id"] != target.task_id or
            payload["revision"] != target.revision or
            payload["spec_sha256"] != target.spec_sha256 or
            payload["source_bundle_sha256"] != target.source_bundle_sha256 or
            not hmac.compare_digest(payload["review_lease_sha256"],target.review_lease_sha256)):
        raise GrantDenied("STALE_OR_CROSS_TASK_SCOPE")
    if (not target.review_lease_active or target.merge_intent_pending):
        raise GrantDenied("REVIEW_STATE_NOT_SAFE")
    if authenticated_actor == target.author_actor_id:
        raise GrantDenied("SELF_REVIEW_FORBIDDEN")
    return {"nonce": payload["nonce"], "task_id": payload["task_id"],
            "action": payload["action"], "expires_at": payload["expires_at"]}


class GrantLedger:
    """One-shot reservation. A crash leaves RESERVED, requiring owner recovery.

    This intentionally cannot issue/repair grants, run QA, or apply code.
    The *separate trusted service* reserves before any irreversible effect.
    Never call a side-effecting merge from the broad legacy MCP process.
    """
    def __init__(self, db_path: Path, key: bytes):
        self.db_path = Path(db_path)
        if type(key) is not bytes or len(key) < 32:
            raise GrantDenied("TRUSTED_KEY_UNAVAILABLE")
        self._key = key

    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("CREATE TABLE IF NOT EXISTS grant_uses ("
                   "nonce TEXT PRIMARY KEY, workspace TEXT NOT NULL,"
                   "task_id TEXT NOT NULL, revision INTEGER NOT NULL,"
                   "action TEXT NOT NULL, state TEXT NOT NULL,"
                   "recorded_at INTEGER NOT NULL,"
                   "UNIQUE(workspace,task_id,revision))")
        return db

    def inspect_signed_grant(self, envelope: dict, *, authenticated_actor: str,
                             target: ReviewTarget, now: int | None = None) -> dict:
        """Verification only, not authority to perform QA or accept code."""
        return verify(envelope, self._key, authenticated_actor=authenticated_actor,
                      target=target, now=now)

    def reserve(self, envelope: dict, *, authenticated_actor: str,
                target: ReviewTarget, now: int | None = None) -> dict:
        validated = self.inspect_signed_grant(
            envelope, authenticated_actor=authenticated_actor,
            target=target, now=now)
        moment = int(time.time()) if now is None else now
        with closing(self._connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # Reverify timing after waiting for the write lock.
                if int(time.time()) >= validated["expires_at"] and now is None:
                    raise GrantDenied("GRANT_EXPIRED_AFTER_LOCK")
                db.execute("INSERT INTO grant_uses "
                           "(nonce,workspace,task_id,revision,action,state,recorded_at)"
                           " VALUES(?,?,?,?,?,?,?)",
                           (validated["nonce"], target.workspace, validated["task_id"],
                            target.revision, validated["action"], "RESERVED", moment))
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise GrantDenied("GRANT_ALREADY_SPENT") from exc
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return {"status": "RESERVED_REQUIRES_PRIVILEGED_QA",
                "task_id": validated["task_id"], "action": validated["action"],
                "one_shot": True, "formal_review_performed": False}

    def state(self, nonce: str) -> str:
        if type(nonce) is not str or not NONCE.fullmatch(nonce):
            raise GrantDenied("INVALID_NONCE")
        with closing(self._connection()) as db:
            row = db.execute("SELECT state FROM grant_uses WHERE nonce=?", (nonce,)).fetchone()
        return row[0] if row else "UNKNOWN"
