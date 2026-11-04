"""Opt-in, fail-closed report-delivery ACK for browser companion.

This does NOT perform trusted P085 host identity verification or formal review
acceptance. It only records locally verified delivery of a scoped report, using
the existing standalone receipt-store concept of immutable content-bound ACK.
The canonical coordinator remains untouched.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"completion-[0-9a-f-]{36}\.md\Z")
_ID = re.compile(r"[0-9a-f-]{36}\Z")
_MAX = 256 * 1024
_MIN = 100
_RESTRICTED = {"CON", "PRN", "AUX", "NUL"}

class Pending(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def _uuid(s: str) -> str:
    if type(s) is not str or not _ID.fullmatch(s):
        raise Pending("invalid_job_id")
    try:
        value = str(uuid.UUID(s))
    except ValueError:
        raise Pending("invalid_job_id") from None
    if s != value:
        raise Pending("invalid_job_id")
    return value

def _read_report(report_root: Path, jid: str) -> tuple[str, int]:
    """Read only the exact expected regular file; never follow symlinks."""
    root = report_root.resolve(strict=True)
    name = f"completion-{jid}.md"
    if not _NAME.fullmatch(name):
        raise Pending("unsafe_path")
    if root.is_symlink() or report_root.is_symlink():
        raise Pending("unsafe_root")
    target = root / name
    try:
        if target.is_symlink():
            raise Pending("report_symlink")
        fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        raise Pending("report_not_found") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not _MIN <= info.st_size <= _MAX:
            raise Pending("report_size_invalid")
        # On Windows, avoid trusting a path replaced with a symlink after check.
        # Safe host ACL/isolation remains a prerequisite for trusted acceptance.
        if target.is_symlink():
            raise Pending("report_symlink")
        payload = os.read(fd, _MAX + 1)
    finally:
        os.close(fd)
    if not _MIN <= len(payload) <= _MAX:
        raise Pending("report_size_invalid")
    try:
        body = payload.decode("utf-8", "strict")
    except UnicodeError:
        raise Pending("report_encoding_invalid") from None
    if f"Job ID: {jid}" not in body or "## Outcome" not in body or "## Evidence" not in body:
        raise Pending("report_binding_invalid")
    return hashlib.sha256(payload).hexdigest(), len(payload)

def _load_job(active_root: Path, jid: str) -> dict:
    path = active_root / (jid + ".json")
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Pending("not_active") from None
    except (OSError, ValueError):
        raise Pending("job_unreadable") from None
    if job.get("id") != jid or job.get("completion_mode") != "report":
        raise Pending("not_completion_job")
    expected = f"completion-{jid}.md"
    if job.get("report_name") != expected:
        raise Pending("report_name_mismatch")
    return job

def evaluate(active_root: Path, report_root: Path, ack_root: Path, job_id: str) -> dict:
    """Idempotent integrity ACK. Formal approval remains a separate workflow."""
    jid = _uuid(job_id)
    _load_job(active_root, jid)
    ack_root.mkdir(exist_ok=True, parents=True)
    target = ack_root / (jid + ".json")
    if target.is_symlink():
        raise Pending("ack_symlink")
    if target.exists():
        try:
            original = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise Pending("ack_corrupt")
        if original.get("job_id") != jid or original.get("status") != "REPORT_ACKED":
            raise Pending("ack_invalid")
        try:
            actual_sha, size = _read_report(report_root, jid)
        except Pending as e:
            raise Pending("accepted_report_unavailable") from e
        if actual_sha != original.get("artifact_sha256") or size != original.get("byte_count"):
            raise Pending("accepted_report_changed")
        return original
    sha, size = _read_report(report_root, jid)
    ack = {
        "schema_version": 1,
        "status": "REPORT_ACKED",
        "trust_level": "advisory_artifact_integrity_only",
        "job_id": jid,
        "artifact_relpath": f"agent-reports/completion-{jid}.md",
        "artifact_sha256": sha,
        "byte_count": size,
        "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ack_id": hashlib.sha256((jid+":"+sha).encode()).hexdigest()[:32],
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    encoded = (json.dumps(ack, ensure_ascii=True, sort_keys=True)+"\n").encode()
    try:
        fd = os.open(str(target), flags, 0o600)
    except FileExistsError:
        # Another concurrent poll wrote it; compare the immutable result.
        return evaluate(active_root, report_root, ack_root, jid)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        # Leave a visible invalid record for explicit reconciliation if write
        # was interrupted, rather than auto-delete/auto-reaccept the result.
        raise
    return ack
