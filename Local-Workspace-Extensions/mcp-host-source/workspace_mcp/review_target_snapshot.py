"""P085 read-only *host trust boundary* input snapshot.

This is a source-of-truth adapter for a future SEPARATE privileged reviewer
service, NOT an MCP tool and NOT an authorization decision. It deliberately
doesn't know or return any reviewer lease token, private credentials, or grants.

Only a host-isolated process can safely supply the DB path, role-to-principal
map and authenticated caller; in the present legacy MCP user identity the
reader's output is observational and MUST NOT authorize review().
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .review_grants import ReviewTarget, GrantDenied

TASK = re.compile(r"P[0-9]{3,6}\Z")
ROLE = re.compile(r"developer-[1-4]\Z")
NAME = re.compile(r"[a-z][a-z0-9_]*\.py\Z")
RELATIVE = re.compile(r"[a-zA-Z0-9_.-]+\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 12
MAX_METADATA_BYTES = 128 * 1024


def _regular(root: Path, rel: str, *, max_bytes: int) -> bytes:
    """Read bounded bytes; reject symlink/junction at every path component."""
    if (type(rel) is not str or len(rel) > 512 or
            not rel or "\\" in rel or ":" in rel or
            rel.startswith("/") or
            any(not RELATIVE.fullmatch(part) or part in (".", "..")
                for part in rel.split("/"))):
        raise GrantDenied("UNSAFE_REVIEW_PATH")
    try:
        root = Path(root).resolve(strict=True)
        path = root
        for part in rel.split("/"):
            path = path / part
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise GrantDenied("LINKED_REVIEW_PATH")
        if (not path.is_file() or not path.resolve(strict=True).is_relative_to(root)
                or path.stat().st_size > max_bytes):
            raise GrantDenied("REVIEW_FILE_MISSING_OR_OVERSIZED")
        with path.open("rb") as fd:
            data = fd.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise GrantDenied("REVIEW_FILE_MISSING_OR_OVERSIZED")
        return data
    except (OSError, RuntimeError) as exc:
        raise GrantDenied("REVIEW_FILE_UNREADABLE") from exc


def _metadata(raw: str, kind: str):
    if type(raw) is not str or len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
        raise GrantDenied("INVALID_REVIEW_METADATA")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise GrantDenied("INVALID_REVIEW_METADATA") from exc
    if type(value) is not dict and kind == "baseline":
        raise GrantDenied("INVALID_REVIEW_METADATA")
    if type(value) is not list and kind == "deliverables":
        raise GrantDenied("INVALID_REVIEW_METADATA")
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(value: str) -> datetime:
    if type(value) is not str or len(value) > 44:
        raise GrantDenied("INVALID_REVIEW_LEASE_TIME")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("naive")
        return t.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise GrantDenied("INVALID_REVIEW_LEASE_TIME") from exc


def snapshot(project: Path, database: Path, task_id: str, *,
             workspace: str, owner_principals: dict,
             now: datetime | None = None) -> ReviewTarget:
    """Reconstruct the signed scope from trusted *current* DB and file bytes.

    Read-only per call; the actual privileged review finalizer MUST recheck
    the same revision/sha/lease inside its transactional acceptance boundary.
    """
    if type(task_id) is not str or not TASK.fullmatch(task_id):
        raise GrantDenied("INVALID_TASK_ID")
    if type(workspace) is not str or not 1 <= len(workspace) <= 40:
        raise GrantDenied("INVALID_WORKSPACE")
    if (type(owner_principals) is not dict
            or set(owner_principals) != {f"developer-{i}" for i in range(1, 5)}
            or any(type(v) is not str or len(v) < 3 or
                   not re.fullmatch(r"[A-Za-z0-9_.:@-]{3,128}", v)
                   for v in owner_principals.values())):
        raise GrantDenied("TRUSTED_AUTHOR_MAPPING_MISSING")
    moment = datetime.now(timezone.utc) if now is None else now
    if type(moment) is not datetime or moment.tzinfo is None:
        raise GrantDenied("INVALID_CLOCK")
    base = Path(project).resolve(strict=True)
    dbfile = Path(database)
    if (dbfile.is_symlink() or getattr(dbfile, "is_junction", lambda: False)()
            or not dbfile.is_file()):
        raise GrantDenied("TRUSTED_DB_UNAVAILABLE")
    try:
        with closing(sqlite3.connect(dbfile.resolve().as_uri() + "?mode=ro",
                                      uri=True, timeout=4)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT task_id,owner,status,review_token,review_until,"
                "revision,sandbox,deliverables,baseline,spec_ref "
                "FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    except sqlite3.Error as exc:
        raise GrantDenied("TRUSTED_DB_UNAVAILABLE") from exc
    if row is None:
        raise GrantDenied("TASK_NOT_FOUND")
    if (row["status"] != "reviewing"
            or type(row["revision"]) is not int or row["revision"] < 0
            or not isinstance(row["review_token"], str)
            or len(row["review_token"]) < 16
            or _utc(row["review_until"]) <= moment.astimezone(timezone.utc)):
        raise GrantDenied("REVIEW_LEASE_NOT_ACTIVE")
    author = row["owner"]
    if type(author) is not str or not ROLE.fullmatch(author):
        raise GrantDenied("AUTHOR_IDENTITY_UNMAPPED")
    spec_ref = row["spec_ref"]
    if type(spec_ref) is not str or " / " not in spec_ref:
        raise GrantDenied("INVALID_SPEC_REF")
    spec_rel = spec_ref.partition(" / ")[0]
    if not spec_rel.startswith(("openspec/", "specs/")):
        raise GrantDenied("SPEC_OUTSIDE_CANONICAL_ROOT")
    spec_digest = _sha(_regular(base, spec_rel, max_bytes=MAX_FILE_BYTES))
    baseline = _metadata(row["baseline"], "baseline")
    if baseline.get("__spec_sha__") != spec_digest:
        raise GrantDenied("SPEC_CHANGED_SINCE_ACQUIRE")
    entries = _metadata(row["deliverables"], "deliverables")
    if (not 2 <= len(entries) <= MAX_FILES
            or len(entries) != len(set(map(str, entries)))
            or any(type(x) is not str or not NAME.fullmatch(x) for x in entries)
            or not any(x.startswith("test_") for x in entries)
            or not any(not x.startswith("test_") for x in entries)):
        raise GrantDenied("INVALID_DELIVERABLE_LIST")
    sandbox = row["sandbox"]
    expected = f".agent-team/workspaces/{author}/{task_id}"
    if sandbox != expected:
        raise GrantDenied("SANDBOX_SCOPE_MISMATCH")
    # Read-only worktree freshness: every canonical main file must still be
    # identical to the saved baseline. For new files baseline must be None.
    digests = []
    for name in sorted(entries):
        candidate = _sha(_regular(base, sandbox + "/" + name, max_bytes=MAX_FILE_BYTES))
        wanted = baseline.get(name, "<missing>")
        if wanted == "<missing>" or (wanted is not None and
                                     (type(wanted) is not str or not SHA.fullmatch(wanted))):
            raise GrantDenied("INVALID_BASELINE")
        main = base / name
        if main.is_symlink() or getattr(main, "is_junction", lambda: False)():
            raise GrantDenied("LINKED_MAIN_FILE")
        if main.exists():
            actual_main = _sha(_regular(base, name, max_bytes=MAX_FILE_BYTES))
        else:
            actual_main = None
        if actual_main != wanted:
            raise GrantDenied("MAIN_BASELINE_CHANGED")
        digests.append([name, candidate])
    canonical = json.dumps(digests, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    bundle_sha = _sha(canonical)
    # Canonical MergeIntent.pending() means *folder presence*, even an
    # incomplete intent with no manifest. Inspect its location without
    # creating folders or interpreting untrusted contents.
    state = base / ".agent-team"
    intent_root = state / "merge-intents"
    for item in (state, intent_root, intent_root / task_id):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise GrantDenied("LINKED_MERGE_INTENT")
    if not state.is_dir():
        raise GrantDenied("STATE_DIR_MISSING")
    if intent_root.exists() and not intent_root.is_dir():
        raise GrantDenied("INVALID_MERGE_INTENT_ROOT")
    pending = (intent_root / task_id).exists()
    return ReviewTarget(workspace=workspace, task_id=task_id,
                        revision=row["revision"], spec_sha256=spec_digest,
                        source_bundle_sha256=bundle_sha,
                        review_lease_sha256=_sha(row["review_token"].encode("utf-8")),
                        review_lease_active=True, merge_intent_pending=pending,
                        author_actor_id=owner_principals[author])
