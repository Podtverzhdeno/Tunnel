"""Immutable, content-addressed local evidence receipts; NOT trusted attestation.

For review packets and deterministic replay only. Creating an evidence blob
never authorizes a task, modifies the real SQLite coordinator or authenticates
the sender. Files are create-only, with no overwrite or automatic repair.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path

try:
    from .transition import ProtocolError, canonical, validate_digest
except ImportError:
    from transition import ProtocolError, canonical, validate_digest

KIND = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
MAX_BYTES = 128 * 1024
DENIED_KEYS = frozenset({"password", "secret", "token", "api_key",
                        "authorization", "cookie", "private_key",
                        "bearer", "access_token", "refresh_token",
                        "lease_token", "raw_prompt", "credential"})

class EvidenceError(ValueError):
    pass

def _check_secrets(obj: object, depth: int = 0) -> None:
    if depth > 10:
        raise EvidenceError("EVIDENCE_DEPTH_EXCEEDED")
    if isinstance(obj, dict):
        if any(not isinstance(k, str) for k in obj):
            raise EvidenceError("EVIDENCE_KEY_INVALID")
        for k,v in obj.items():
            if k.casefold() in DENIED_KEYS:
                raise EvidenceError("EVIDENCE_SENSITIVE_FIELD_DENIED")
            _check_secrets(v, depth+1)
    elif isinstance(obj, (tuple, list)):
        for v in obj:
            _check_secrets(v, depth+1)

def _directory(path: Path) -> Path:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise EvidenceError("EVIDENCE_LINK_DENIED")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("EVIDENCE_DIRECTORY_MISSING") from exc
    if not root.is_dir():
        raise EvidenceError("EVIDENCE_NOT_DIRECTORY")
    return root

class EvidenceStore:
    def __init__(self, root: str | Path):
        root = _directory(Path(root))
        store = root / "evidence_immutable"
        if store.is_symlink() or getattr(store, "is_junction", lambda: False)():
            raise EvidenceError("EVIDENCE_LINK_DENIED")
        store.mkdir(exist_ok=True)
        self.root = _directory(store)
        if not self.root.is_relative_to(root):
            raise EvidenceError("EVIDENCE_PATH_ESCAPE")

    def _target(self, fingerprint: str, *, create: bool = False) -> Path:
        if not isinstance(fingerprint, str) or not SHA.fullmatch(fingerprint):
            raise EvidenceError("EVIDENCE_ID_INVALID")
        directory = self.root / fingerprint[:2]
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise EvidenceError("EVIDENCE_LINK_DENIED")
        if create:
            directory.mkdir(exist_ok=True)
        if directory.resolve(strict=False).parent != self.root:
            raise EvidenceError("EVIDENCE_PATH_ESCAPE")
        path = directory / (fingerprint + ".json")
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise EvidenceError("EVIDENCE_LINK_DENIED")
        return path

    def put(self, kind: str, source_sha: str, payload: dict) -> dict:
        if not isinstance(kind, str) or not KIND.fullmatch(kind):
            raise EvidenceError("EVIDENCE_KIND_INVALID")
        validate_digest(source_sha, required=True)
        if not isinstance(payload, dict):
            raise EvidenceError("EVIDENCE_PAYLOAD_INVALID")
        _check_secrets(payload)
        document = {
            "schema_version": 1, "kind": kind,
            "source_sha": source_sha, "payload": payload,
            "provenance": "UNVERIFIED_LOCAL",
        }
        try:
            raw = canonical(document)
        except (TypeError, OverflowError, ValueError) as exc:
            raise EvidenceError("EVIDENCE_JSON_INVALID") from exc
        if len(raw) > MAX_BYTES:
            raise EvidenceError("EVIDENCE_TOO_LARGE")
        fingerprint = hashlib.sha256(raw).hexdigest()
        path = self._target(fingerprint, create=True)
        created = False
        try:
            with path.open("xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            created = True
        except FileExistsError:
            pass
        # Re-read independently. On an interrupted partial write, fail
        # without silently overwriting a potentially shared artifact.
        checked = self.verify(fingerprint)
        if not checked["ok"]:
            raise EvidenceError("EVIDENCE_INTEGRITY_FAILURE")
        return {"ok": True, "sha256": fingerprint, "created": created,
                "bytes": len(raw), "authenticated": False}

    def verify(self, fingerprint: str) -> dict:
        path = self._target(fingerprint)
        if not path.is_file():
            return {"ok": False, "error": "EVIDENCE_NOT_FOUND",
                    "authenticated": False}
        if path.stat().st_size > MAX_BYTES:
            return {"ok": False, "error": "EVIDENCE_OVERSIZED",
                    "authenticated": False}
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != fingerprint:
            return {"ok": False, "error": "EVIDENCE_HASH_DIVERGED",
                    "authenticated": False}
        try:
            obj = json.loads(raw.decode("utf-8"))
            if (not isinstance(obj,dict) or canonical(obj) != raw or
                set(obj) != {"schema_version","kind","source_sha","payload","provenance"} or
                obj.get("schema_version") != 1 or
                obj.get("provenance") != "UNVERIFIED_LOCAL" or
                not isinstance(obj.get("kind"),str) or not KIND.fullmatch(obj["kind"]) or
                not isinstance(obj.get("payload"),dict)):
                raise EvidenceError("EVIDENCE_SCHEMA_DIVERGED")
            validate_digest(obj["source_sha"], required=True)
            _check_secrets(obj)
        except (UnicodeError, ValueError, TypeError, EvidenceError):
            return {"ok": False, "error": "EVIDENCE_SCHEMA_INVALID",
                    "authenticated": False}
        return {"ok": True, "sha256": fingerprint, "kind": obj["kind"],
                "source_sha": obj["source_sha"], "bytes": len(raw),
                "authenticated": False, "source_authenticated": False}
