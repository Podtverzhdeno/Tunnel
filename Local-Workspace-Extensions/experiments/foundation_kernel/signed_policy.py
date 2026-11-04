"""Offline owner/issuer-signed grant-policy validation and monotonic floor.

A signed policy is only as trustworthy as its pinned issuer public key AND
the OS protection of the executable, checkpoint SQLite and key. It does not
grant owner approval or isolate another process running under the same SID.
Private keys are never generated, stored or loaded by this implementation.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    InvalidSignature = None
    Ed25519PublicKey = None

try:
    from .transition import canonical, validate_id, validate_digest
except ImportError:
    from transition import canonical, validate_id, validate_digest

SCHEMA = "broker-signed-policy-v1"
DOMAIN = b"broker-signed-policy-v1\x00"
MAX_BUNDLE = 8192
MAX_POLICY_LIFETIME_SECONDS = 30 * 86400
FLOOR_SCHEMA = "broker-policy-floor-v1"
FLOOR_TABLE = "signed_policy_floor"

class PolicySignatureError(ValueError):
    pass


def _fixed_time(value: object) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise PolicySignatureError("SIGNED_POLICY_TIME_INVALID")
    try:
        return datetime.strptime(value,"%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise PolicySignatureError("SIGNED_POLICY_TIME_INVALID") from exc


def _file(path: Path, max_bytes: int) -> bytes:
    if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
        raise PolicySignatureError("SIGNED_POLICY_LINK_DENIED")
    try:
        stat=path.stat()
        if not path.is_file() or not 2 <= stat.st_size <= max_bytes:
            raise PolicySignatureError("SIGNED_POLICY_FILE_INVALID")
        raw=path.read_bytes()
    except OSError as exc:
        raise PolicySignatureError("SIGNED_POLICY_UNAVAILABLE") from exc
    if len(raw)!=stat.st_size:
        raise PolicySignatureError("SIGNED_POLICY_FILE_CHANGED")
    return raw


def _unique_pairs(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:
            raise PolicySignatureError("SIGNED_POLICY_DUPLICATE_FIELD")
        obj[k]=v
    return obj


def verify_signed_policy(bundle: Path, policy: Path, issuer_public_hex: str,
                         now: datetime) -> dict:
    """Verify signature and exact policy byte digest; no writes."""
    if Ed25519PublicKey is None:
        raise PolicySignatureError("SIGNED_POLICY_CRYPTO_UNAVAILABLE")
    if (type(issuer_public_hex) is not str or len(issuer_public_hex)!=64 or
        any(x not in "0123456789abcdef" for x in issuer_public_hex)):
        raise PolicySignatureError("SIGNED_POLICY_ISSUER_KEY_INVALID")
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise PolicySignatureError("SIGNED_POLICY_TRUSTED_CLOCK_REQUIRED")
    raw=_file(Path(bundle), MAX_BUNDLE)
    policy_raw=_file(Path(policy),32768)
    try:
        data=json.loads(raw.decode("utf-8"),object_pairs_hook=_unique_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            PolicySignatureError("SIGNED_POLICY_NUMBER_INVALID")))
    except (ValueError,UnicodeError,TypeError) as exc:
        raise PolicySignatureError("SIGNED_POLICY_JSON_INVALID") from exc
    required={
        "schema","issuer_id","revision","issued_utc","expires_utc",
        "policy_sha256","signature_b64",
    }
    if type(data) is not dict or set(data)!=required or data["schema"]!=SCHEMA:
        raise PolicySignatureError("SIGNED_POLICY_SCHEMA_INVALID")
    try:
        validate_id(data["issuer_id"],"ISSUER_ID")
        validate_digest(data["policy_sha256"],required=True)
    except ValueError as exc:
        raise PolicySignatureError("SIGNED_POLICY_FIELDS_INVALID") from exc
    revision=data["revision"]
    if type(revision) is not int or not 1<=revision<=2**31-1:
        raise PolicySignatureError("SIGNED_POLICY_REVISION_INVALID")
    issued=_fixed_time(data["issued_utc"])
    expiry=_fixed_time(data["expires_utc"])
    seconds=(expiry-issued).total_seconds()
    if not 0 < seconds <= MAX_POLICY_LIFETIME_SECONDS:
        raise PolicySignatureError("SIGNED_POLICY_WINDOW_INVALID")
    clock=now.astimezone(timezone.utc)
    if clock < issued or clock > expiry:
        raise PolicySignatureError("SIGNED_POLICY_EXPIRED_OR_NOT_ACTIVE")
    digest=hashlib.sha256(policy_raw).hexdigest()
    if data["policy_sha256"]!=digest:
        raise PolicySignatureError("SIGNED_POLICY_CONTENT_MISMATCH")
    encoded=data["signature_b64"]
    if type(encoded) is not str or len(encoded)>128:
        raise PolicySignatureError("SIGNED_POLICY_SIGNATURE_FORMAT_INVALID")
    try:
        signature=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError) as exc:
        raise PolicySignatureError("SIGNED_POLICY_SIGNATURE_FORMAT_INVALID") from exc
    if len(signature)!=64 or base64.b64encode(signature).decode("ascii")!=encoded:
        raise PolicySignatureError("SIGNED_POLICY_SIGNATURE_FORMAT_INVALID")
    body={k:v for k,v in data.items() if k!="signature_b64"}
    if len(canonical(body))>MAX_BUNDLE:
        raise PolicySignatureError("SIGNED_POLICY_TOO_LARGE")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(issuer_public_hex)).verify(
            signature,DOMAIN+canonical(body))
    except (ValueError,InvalidSignature) as exc:
        raise PolicySignatureError("SIGNED_POLICY_SIGNATURE_INVALID") from exc
    return {
        "ok":True,"revision":revision,"issuer_id":data["issuer_id"],
        "issuer_key_sha":hashlib.sha256(bytes.fromhex(issuer_public_hex)).hexdigest(),
        "policy_sha":digest,"issued_utc":data["issued_utc"],
        "expires_utc":data["expires_utc"],
        "signature_verified":True,
        "issuer_provisioning_authenticated":False,
    }


class SignedPolicyFloor:
    """A monotonic floor in the SAME SQLite DB as the atomically staged work."""

    def __init__(self, db: sqlite3.Connection):
        if not isinstance(db,sqlite3.Connection) or db.in_transaction:
            raise PolicySignatureError("POLICY_FLOOR_REQUIRES_IDLE_SQLITE")
        self.db=db
        existing={x[0] for x in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        tables={FLOOR_TABLE,"signed_policy_floor_meta"}
        if existing.intersection(tables):
            if not tables.issubset(existing):
                raise PolicySignatureError("POLICY_FLOOR_SCHEMA_INCOMPLETE")
            try:
                meta=db.execute(
                    "SELECT value FROM signed_policy_floor_meta WHERE key='protocol'"
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise PolicySignatureError("POLICY_FLOOR_INVALID_SCHEMA") from exc
            if meta!=(FLOOR_SCHEMA,):
                raise PolicySignatureError("POLICY_FLOOR_PROTOCOL_INVALID")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS signed_policy_floor_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS signed_policy_floor (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                issuer_key_sha TEXT NOT NULL,
                revision INTEGER NOT NULL,
                policy_sha TEXT NOT NULL);
        """)
        if not existing.intersection(tables):
            db.execute(
                "INSERT INTO signed_policy_floor_meta(key,value) VALUES('protocol',?)",
                (FLOOR_SCHEMA,))

    def admit(self, verified: dict) -> tuple[str,int,str]:
        if (type(verified) is not dict or verified.get("ok") is not True or
            verified.get("signature_verified") is not True):
            raise PolicySignatureError("POLICY_FLOOR_VERIFIED_INPUT_REQUIRED")
        issuer,revision,digest=(
            verified.get("issuer_key_sha"),verified.get("revision"),
            verified.get("policy_sha"))
        for value in (issuer,digest):
            try: validate_digest(value,required=True)
            except ValueError as exc:
                raise PolicySignatureError("POLICY_FLOOR_DIGEST_INVALID") from exc
        if type(revision) is not int or not 1<=revision<=2**31-1:
            raise PolicySignatureError("POLICY_FLOOR_REVISION_INVALID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old=self.db.execute(
                "SELECT issuer_key_sha,revision,policy_sha FROM signed_policy_floor "
                "WHERE singleton=1").fetchone()
            if old is not None:
                if old[0]!=issuer:
                    raise PolicySignatureError("POLICY_FLOOR_ISSUER_CHANGED")
                if revision < old[1]:
                    raise PolicySignatureError("POLICY_FLOOR_ROLLBACK_DENIED")
                if revision == old[1] and digest!=old[2]:
                    raise PolicySignatureError("POLICY_FLOOR_EQUIVOCATION_DENIED")
            if old is None or revision>old[1]:
                self.db.execute(
                    "INSERT INTO signed_policy_floor("
                    "singleton,issuer_key_sha,revision,policy_sha) "
                    "VALUES (1,?,?,?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "issuer_key_sha=excluded.issuer_key_sha,"
                    "revision=excluded.revision,policy_sha=excluded.policy_sha",
                    (issuer,revision,digest))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return issuer,revision,digest


def verify_current_floor_locked(db: sqlite3.Connection,
                                expected: tuple[str,int,str]) -> None:
    """MUST be called inside the same BEGIN IMMEDIATE as the host mutation."""
    if not isinstance(db,sqlite3.Connection) or not db.in_transaction:
        raise PolicySignatureError("POLICY_FLOOR_TRANSACTION_REQUIRED")
    actual=db.execute(
        "SELECT issuer_key_sha,revision,policy_sha "
        "FROM signed_policy_floor WHERE singleton=1").fetchone()
    if actual!=expected:
        raise PolicySignatureError("POLICY_FLOOR_CHANGED_DURING_SESSION")
