"""Fail-closed, request-scoped CDP Send journal for ISOLATED testing.

Never enables a production worker. No prompt, response, URL, chat identity,
browser cookie, lease/token, or Telegram ID is written. This journal is
additional to (not a replacement for) Relay's lease/intent fences.

Process-crash safety:
* O_EXCL reservation prevents duplicate logical attempts across processes;
* durable ARMED entry is persisted before a potentially irreversible UI click;
* any ambiguous post-arm outcome is UNKNOWN, with no automatic re-arm;
* atomic file replacement + OS-held record lock serialize transitions.

A power-loss durability guarantee is NOT claimed, especially on Windows where
Python cannot portably fsync the parent directory after atomic replace.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import time
import uuid

SCHEMA = "hermes-cdp-send-journal-v1"
PREPARED = "prepared"
ARMED = "send_armed"
BLOCKED = "blocked_pre_send"
UNCERTAIN = "send_outcome_unknown"
VERIFIED = "fixture_response_verified"
PHASES = frozenset({PREPARED, ARMED, BLOCKED, UNCERTAIN, VERIFIED})
TERMINAL = frozenset({BLOCKED, UNCERTAIN, VERIFIED})
REASONS = frozenset({
    "preflight_failed", "page_closed", "worker_crashed",
    "click_or_response_unconfirmed", "fixture_exact_response", "unknown",
})
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class SendJournalError(RuntimeError):
    """Journal integrity, authentication boundary, or unsafe phase transition."""


class AlreadyReserved(SendJournalError):
    """A request has already been attempted: never automatically replay."""


def _key(request_id: str) -> str:
    if not isinstance(request_id, str) or not _UUID4.fullmatch(request_id):
        raise SendJournalError("request_id_must_be_uuid4")
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError("noncanonical")
    except ValueError as exc:
        raise SendJournalError("invalid_request_id") from exc
    return sha256(("hermes-cdp-journal:" + request_id).encode("ascii")).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def _exclusive_lock(path: Path):
    """OS lock released on process termination, including os._exit."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
            os.fsync(fd)
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            for _ in range(250):
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            else:
                raise SendJournalError("record_lock_timeout")
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class SendJournal:
    def __init__(self, directory: str | Path):
        # The caller must provision a protected directory; no ambient profile
        # path, automatic directory creation or access-control changes.
        root = Path(directory)
        if (not root.is_absolute() or str(root).startswith("\\\\") or
                root.is_symlink() or
                bool(getattr(root, "is_junction", lambda: False)()) or
                not root.is_dir()):
            raise SendJournalError("private_local_directory_required")
        for ancestor in root.parents:
            if (ancestor.is_symlink() or
                    bool(getattr(ancestor, "is_junction", lambda: False)())):
                raise SendJournalError("linked_directory_ancestor_forbidden")
        self.root = root.resolve(strict=True)

    def _path(self, request_id: str) -> Path:
        key = _key(request_id)
        return self.root / (key + ".json")

    @staticmethod
    def _validate(record: object, key: str) -> dict:
        if not isinstance(record, dict) or set(record) != {
            "schema", "request_key", "phase", "reason", "generation",
            "send_arms", "updated_utc",
        }:
            raise SendJournalError("record_structure_invalid")
        if record["schema"] != SCHEMA or record["request_key"] != key:
            raise SendJournalError("record_identity_mismatch")
        if record["phase"] not in PHASES:
            raise SendJournalError("record_phase_invalid")
        if not isinstance(record["generation"], int) or isinstance(record["generation"], bool) or record["generation"] < 0:
            raise SendJournalError("record_generation_invalid")
        if (type(record["send_arms"]) is not int or
                record["send_arms"] != (0 if record["phase"] in {PREPARED, BLOCKED} else 1)):
            raise SendJournalError("record_arms_invalid")
        expected_reason = {
            PREPARED: {None},
            ARMED: {None},
            BLOCKED: {"preflight_failed"},
            VERIFIED: {"fixture_exact_response"},
            UNCERTAIN: REASONS - {"fixture_exact_response", "preflight_failed"},
        }
        if record["reason"] not in expected_reason[record["phase"]]:
            raise SendJournalError("record_reason_invalid")
        if not isinstance(record["updated_utc"], str) or len(record["updated_utc"]) > 64:
            raise SendJournalError("record_time_invalid")
        return record

    def read(self, request_id: str) -> dict:
        key = _key(request_id)
        path = self._path(request_id)
        if path.is_symlink():
            raise SendJournalError("symlink_record_forbidden")
        try:
            with path.open("r", encoding="utf-8") as stream:
                record = json.load(stream)
        except (OSError, ValueError, UnicodeError) as exc:
            raise SendJournalError("record_unavailable_or_corrupt_no_replay") from exc
        return self._validate(record, key).copy()

    def reserve(self, request_id: str) -> dict:
        path = self._path(request_id)
        key = _key(request_id)
        record = {
            "schema": SCHEMA, "request_key": key,
            "phase": PREPARED, "reason": None,
            "generation": 0, "send_arms": 0, "updated_utc": _utc(),
        }
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise AlreadyReserved("no_replay_existing_request") from exc
        # Crash during initial write leaves a corrupt record. That is intentional:
        # it cannot be re-reserved, and read() fails closed.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record.copy()

    def _transition(self, request_id: str, expected: str, nxt: str, reason: str | None) -> dict:
        key = _key(request_id)
        if reason is not None and reason not in REASONS:
            raise SendJournalError("untrusted_reason")
        path = self._path(request_id)
        with _exclusive_lock(self.root / (key + ".lock")):
            current = self.read(request_id)
            if current["phase"] != expected:
                raise AlreadyReserved("no_replay_phase_already_advanced")
            proposed = current.copy()
            proposed.update({
                "phase": nxt, "reason": reason,
                "generation": current["generation"] + 1,
                "send_arms": 0 if nxt == BLOCKED else 1,
                "updated_utc": _utc(),
            })
            self._validate(proposed, key)
            tmp = self.root / (key + "." + secrets.token_hex(8) + ".tmp")
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(proposed, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.is_symlink():
                    raise SendJournalError("symlink_record_forbidden")
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            return proposed.copy()

    def block_pre_send(self, request_id: str) -> dict:
        return self._transition(request_id, PREPARED, BLOCKED, "preflight_failed")

    def arm_before_click(self, request_id: str) -> dict:
        """MUST return successfully before any UI click is issued."""
        return self._transition(request_id, PREPARED, ARMED, None)

    def mark_unknown(self, request_id: str, reason: str = "unknown") -> dict:
        if reason not in REASONS - {"fixture_exact_response", "preflight_failed"}:
            raise SendJournalError("unknown_reason_invalid")
        return self._transition(request_id, ARMED, UNCERTAIN, reason)

    def complete_fixture_response(self, request_id: str, *, exact_identity_proven: bool) -> dict:
        """Fixture ONLY. Real ChatGPT requires a different reviewed proof source."""
        if exact_identity_proven is not True:
            raise SendJournalError("exact_fixture_identity_not_proven")
        return self._transition(request_id, ARMED, VERIFIED, "fixture_exact_response")
