"""Read-only causal triage for the *existing* Web Companion v0.6.6 state files.

No extension control, replay, dispatch, disk writes, token reading, prompt capture,
or permissions changes. File snapshots are individually stable checked, but not
atomically consistent across independently updated queues/diagnostic files.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from itertools import islice
import os
from pathlib import Path
import re
import sys
import uuid

BRIDGE_VERSION = "0.6.6"
MAX_FILE_BYTES = 65_536
MAX_FILES_PER_STATE = 500
MAX_EVENTS_PER_JOB = 500
DEFAULT_HEARTBEAT_STALE_SECONDS = 120
DEFAULT_JOB_STALE_SECONDS = 600
JOB_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
SAFE_STAGES = frozenset({
    "tab_opening", "tab_created", "assigned", "editor_found", "prompt_filled",
    "send_clicked", "message_saved", "done", "submitted", "unknown",
    "watchdog_timeout", "receipt_accepted", "receipt_failed", "tab_closed",
    "tab_close_failed", "tab_missing", "tab_close_skipped", "tab_preserved",
    "auth_context_changed", "runtime_error", "report_pending", "report_acked",
    "report_watchdog", "report_disputed", "tab_recovered", "tab_restored",
    "tab_identity_unverified", "tab_binding_disputed", "send_ui_diagnostic",
    "handoff_failed", "ui_cooldown", "paused_after_failures",
})
SAFE_REASONS = frozenset({
    "account_context_changed", "unexpected_close", "unknown", "watchdog_timeout",
    "ambiguous_probe_outcome", "send_button_missing", "editor_not_found",
    "editor_fill_failed", "ui_probe_exhausted", "ui_pre_send_unavailable",
    "tab_identity_unverified", "tab_binding_disputed", "report_disputed",
    "receipt_failed", "bridge_unreachable", "bridge_http_429", "bridge_http_403",
})
AMBIGUOUS = frozenset({
    "unknown", "watchdog_timeout", "auth_context_changed", "report_disputed",
    "tab_identity_unverified", "tab_binding_disputed", "tab_preserved",
})


class MonitorError(ValueError):
    """Never embeds local file contents or paths in operator-visible errors."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    certainty: str
    job_id: str | None = None
    note: str = ""

    def as_dict(self):
        return asdict(self)


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()


def _bounded_json(path: Path, max_bytes: int = MAX_FILE_BYTES):
    """Best effort read of a stable, regular bounded file; never follow symlinks.

    A writer may atomically rename the file while we read. Treat that as unknown
    rather than claiming a root cause. This is not a transaction across files.
    """
    try:
        if not _regular(path):
            return None, "unavailable"
        pre = path.stat()
        if pre.st_size < 2 or pre.st_size > max_bytes:
            return None, "unsafe_or_oversized"
        # O_NOFOLLOW protects the final path component where the platform
        # supports it. This is still not an OS-backed Windows trust boundary.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            body = stream.read(max_bytes + 1)
        post = path.stat()
        stable = (pre.st_dev, pre.st_ino, pre.st_size, pre.st_mtime_ns)
        if (len(body) > max_bytes or
                stable != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) or
                stable != (post.st_dev, post.st_ino, post.st_size, post.st_mtime_ns)):
            return None, "changed_during_read"
        # JSON must not silently replace conflicting duplicate fields.
        def unique(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError("duplicate key")
                result[k] = v
            return result
        return json.loads(body.decode("utf-8"), object_pairs_hook=unique), None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None, "unreadable"


def _utc(value) -> datetime | None:
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        return None
    return dt


def _canonical_id(value) -> bool:
    if not isinstance(value, str) or not JOB_ID.fullmatch(value):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def snapshot(state_root: str | Path, *, now: datetime | None = None,
             heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
             job_stale_seconds: int = DEFAULT_JOB_STALE_SECONDS) -> dict:
    """Produce sanitized best-effort snapshot of existing Web Companion files.

    Read-only and safe for a local operator to run while bridge admissions are
    held; never grants permission to resume, resend, close, or create jobs.
    """
    if (type(heartbeat_stale_seconds) is not int or heartbeat_stale_seconds < 1 or
            type(job_stale_seconds) is not int or job_stale_seconds < 1):
        raise MonitorError("stale thresholds must be positive integers")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise MonitorError("now must be UTC")
    root = Path(state_root)
    if (not root.is_dir() or root.is_symlink() or
            getattr(root, "is_junction", lambda: False)()):
        raise MonitorError("bridge state directory is absent or not trusted")
    findings: list[Finding] = []
    def flag(code, severity, certainty="observed", *, job_id=None, note=""):
        findings.append(Finding(code, severity, certainty, job_id, note))

    hold = root / "admission-hold.json"
    hold_present = hold.exists() or hold.is_symlink()
    if hold_present:
        # No need to read the hold's contents: do not leak private reason text.
        flag("ADMISSION_HOLD_PRESENT", "warning", note="Do not resume or clear holds automatically")
    ext, error = _bounded_json(root / "diagnostics" / "extension.json", 4096)
    extension = {"heartbeat": "unavailable", "version": None, "state": "unknown", "windows": None}
    if error or type(ext) is not dict:
        flag("EXTENSION_HEARTBEAT_UNAVAILABLE", "warning", "hypothesis",
             note="Bridge or extension may be down; no evidence of browser permission")
    else:
        stamp = _utc(ext.get("at"))
        version, state, windows = ext.get("version"), ext.get("state"), ext.get("windows")
        if type(version) is str and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", version):
            extension["version"] = version
        if type(state) is str and state in ("polling", "paused", "disconnected", "idle", "error"):
            extension["state"] = state
        if type(windows) is int and 0 <= windows <= 1000:
            extension["windows"] = windows
        if stamp is None or (now - stamp).total_seconds() < 0:
            flag("EXTENSION_HEARTBEAT_INVALID", "warning", "hypothesis")
        elif (now - stamp).total_seconds() > heartbeat_stale_seconds:
            extension["heartbeat"] = "stale"
            flag("EXTENSION_HEARTBEAT_STALE", "warning", "hypothesis", note="Do not conclude the task failed")
        else:
            extension["heartbeat"] = "recent"
        if extension["version"] != BRIDGE_VERSION:
            flag("EXTENSION_VERSION_MISMATCH", "warning", note="Compare installed extension and bridge required version")
        if extension["state"] != "polling":
            flag("EXTENSION_NOT_POLLING", "warning", "hypothesis")
        if extension["windows"] == 0:
            flag("NO_BROWSER_WINDOWS", "warning", "hypothesis")

    counts = {"queue": 0, "active": 0, "done": 0}
    identifiers: dict[str, set[str]] = {}
    jobs: dict[str, dict] = {}
    for folder in counts:
        directory = root / folder
        if not directory.exists():
            continue
        if not directory.is_dir() or directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            flag("STATE_FOLDER_UNSAFE", "warning", "hypothesis")
            continue
        # Never materialize an unbounded directory listing into memory. With
        # truncation we cannot promise that this is a complete state snapshot.
        files = sorted(islice(directory.glob("*.json"), MAX_FILES_PER_STATE + 1))
        if len(files) > MAX_FILES_PER_STATE:
            flag("STATE_SCAN_TRUNCATED", "warning", "observed")
            files = files[:MAX_FILES_PER_STATE]
        for path in files:
            job_id = path.stem
            if not _canonical_id(job_id):
                flag("INVALID_JOB_FILENAME", "warning", "observed")
                continue
            counts[folder] += 1
            identifiers.setdefault(job_id, set()).add(folder)
            obj, err = _bounded_json(path)
            jobs.setdefault(job_id, {"states": set(), "record": None, "errors": []})
            jobs[job_id]["states"].add(folder)
            if err or type(obj) is not dict or obj.get("id") != job_id:
                jobs[job_id]["errors"].append("JOB_RECORD_UNREADABLE")
                flag("JOB_RECORD_UNREADABLE", "warning", "hypothesis", job_id=job_id)
            elif jobs[job_id]["record"] is None:
                # Extract exactly the supported shape: no prompt/URL leakage.
                jobs[job_id]["record"] = {
                    "completion_mode": obj.get("completion_mode") == "report",
                    "status": obj.get("status") if obj.get("status") in ("done", "submitted", "unknown") else "unknown",
                }
    for job_id, states in identifiers.items():
        if len(states) > 1:
            flag("STATE_DUPLICATE_OR_TRANSITION", "warning", "hypothesis", job_id=job_id,
                 note="A concurrent queue move can resemble a duplicate; resnapshot before any action")

    for job_id, item in sorted(jobs.items()):
        event_file = root / "diagnostics" / (job_id + ".json")
        raw_events, error = _bounded_json(event_file)
        if error or type(raw_events) is not list or len(raw_events) > MAX_EVENTS_PER_JOB:
            flag("JOB_TELEMETRY_UNAVAILABLE", "warning", "hypothesis", job_id=job_id)
            continue
        events = [e for e in raw_events if type(e) is dict and type(e.get("stage")) is str
                  and e["stage"] in SAFE_STAGES]
        last_stage = events[-1]["stage"] if events else None
        reasons = {e["reason"] for e in events if type(e.get("reason")) is str
                   and e["reason"] in SAFE_REASONS}
        stages = {e["stage"] for e in events}
        if "account_context_changed" in reasons or "auth_context_changed" in stages:
            flag("ACCOUNT_CONTEXT_DISPUTED", "critical", job_id=job_id,
                 note="Require explicit owner identity reconciliation")
        if stages & AMBIGUOUS:
            flag("AMBIGUOUS_EXTERNAL_OUTCOME", "critical", job_id=job_id,
                 note="Never resend existing job before receipt reconciliation")
        if "active" in item["states"] and ("send_clicked" in stages or "message_saved" in stages):
            if not ("receipt_accepted" in stages or "report_acked" in stages):
                flag("POSSIBLY_SENT_AWAITING_RECEIPT", "warning", "hypothesis", job_id=job_id,
                     note="No replay; check bridge/ACK before classifying")
        if "done" in item["states"] and item["record"] and item["record"]["completion_mode"]:
            ack = root / "completion-acks" / (job_id + ".json")
            ack_data, ack_error = _bounded_json(ack)
            if ack_error or type(ack_data) is not dict or ack_data.get("status") != "REPORT_ACKED":
                flag("DONE_REPORT_ACK_UNVERIFIED", "critical", "hypothesis", job_id=job_id,
                     note="Snapshot cannot validate report SHA; defer to bridge receipt verifier")
        # Time is local bridge telemetry, not authenticated provider timing.
        if "active" in item["states"] and events:
            recent = _utc(events[-1].get("at"))
            if recent is not None and (now - recent).total_seconds() >= job_stale_seconds:
                flag("ACTIVE_JOB_TELEMETRY_SILENCE", "warning", "hypothesis", job_id=job_id)
        item["last_stage"] = last_stage
        # Bounded and redacted causal crumbs. Source timestamps are not trusted
        # collector offsets, and are NOT proof of provider execution.
        item["recent_events"] = [
            {"stage": e["stage"],
             **({"reason": e["reason"]} if type(e.get("reason")) is str
                and e["reason"] in SAFE_REASONS else {}),
             **({"at": e["at"]} if _utc(e.get("at")) is not None else {})}
            for e in events[-5:]
        ]
    findings.sort(key=lambda f: (f.job_id or "", f.code))
    job_summaries = [{
        "job_id": job_id,
        "reported_states": sorted(item["states"]),
        "last_stage": item.get("last_stage"),
        "recent_events": item.get("recent_events", []),
        "record_readable": item["record"] is not None,
        "completion_mode": (item["record"] or {}).get("completion_mode"),
        "recorded_status": (item["record"] or {}).get("status"),
    } for job_id, item in sorted(jobs.items())]
    return {
        "schema_version": 1,
        "source": "web_companion_local_snapshot",
        "read_only": True,
        "atomic_across_files": False,
        "browser_or_provider_outcome_trusted": False,
        "at": now.isoformat(),
        "extension": extension,
        "admission_hold_present": hold_present,
        "counts": counts,
        "job_count_scanned": len(jobs),
        "job_summaries": job_summaries,
        "findings": [f.as_dict() for f in findings],
        "safe_to_autoretry_unknown_job": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Web Companion stop/recovery diagnostics")
    parser.add_argument("--state", default=str(Path(__file__).parent / "bridge-state"))
    parser.add_argument("--heartbeat-stale", type=int, default=DEFAULT_HEARTBEAT_STALE_SECONDS)
    parser.add_argument("--job-stale", type=int, default=DEFAULT_JOB_STALE_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = snapshot(args.state, heartbeat_stale_seconds=args.heartbeat_stale,
                          job_stale_seconds=args.job_stale)
    except (MonitorError, OSError, ValueError, TypeError):
        print(json.dumps({"ok": False, "error_code": "OBSERVER_INPUT_INVALID"}))
        return 2
    print(json.dumps({"ok": not any(x["severity"] == "critical" for x in result["findings"]),
                      **result}, ensure_ascii=False))
    return 1 if any(x["severity"] == "critical" for x in result["findings"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
