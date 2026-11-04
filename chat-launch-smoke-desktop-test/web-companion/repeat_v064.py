"""Finite, user-requested v0.6.4 live browser repeat lane.

Prepared jobs live in a SEPARATE queue. The existing admission hold, 17 active
legacy jobs and 8 legacy queued jobs remain untouched. While held, only the
four UUIDs listed in a short-lived manifest may be claimed by bridge's LOCK.

No recurrent job generator, no retry of any previously claimed job, no
automatic role descendants. This module is stdlib only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
STATE = ROOT / "bridge-state"
EXPERIMENT = "repeat-v064-20261103"
LANE = STATE / EXPERIMENT
QUEUE = LANE / "queue"
PERMIT = LANE / "permit.json"
ACTIVE = STATE / "active"
DONE = STATE / "done"
LEGACY_QUEUE = STATE / "queue"
HOLD = STATE / "admission-hold.json"
BRIEFS = PROJECT / "agent-briefs"
WORK = PROJECT / "experiments" / EXPERIMENT
DIAG = STATE / "diagnostics"
IDS = tuple(str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT + "/" + str(n))) for n in range(1, 5))
ROLES = (
    "repeat-v064-01-binding",
    "repeat-v064-02-reconcile",
    "repeat-v064-03-faults",
    "repeat-v064-04-review",
)
GOALS = (
    "Implement an isolated, portable reference model for durable job-to-tab ownership, "
    "including unique DOM proof, safe URL matching, reboot, duplicate-tab and tab-ID-reuse tests.",
    "Implement a crash-safe, idempotent state machine for sent/uncertain/ACK/DONE browser jobs, "
    "including durable event replay, ambiguous completion and safe/manual reconciliation.",
    "Build an independent 25-worker fault-injection simulator with bounded concurrency, "
    "fairness/latency measures, disconnected browsers and exactly-once safety assertions.",
    "Perform adversarial read-only code review of v0.6.4 extension recovery logic and bridge. "
    "Create standalone reproducible negative tests and evidence-based feature recommendations. "
    "Do not edit the extension or bridge.",
)
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
N = 4
MAX_ACTIVE = 25


class TrialError(RuntimeError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def heartbeat_ready():
    try:
        d = json.loads((DIAG / "extension.json").read_text(encoding="utf-8"))
        date = datetime.fromisoformat(d["at"])
        age = (utcnow() - date).total_seconds()
        return (d.get("version") in ("0.6.4","0.6.5","0.6.6") and d.get("state") == "polling"
                and type(d.get("windows")) is int and d["windows"] > 0
                and 0 <= age <= 120)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def report_name(jid):
    return "completion-" + jid + ".md"


def brief(role, goal):
    area = f"chat-launch-smoke-desktop-test/experiments/{EXPERIMENT}/{role}/"
    return f"""# v0.6.4 Live Repeat — {role}

## Objective
{goal}

## Requirements
This is an independent substantial engineering assignment, not a countdown.
Design a cohesive architecture and implement it in your own isolated workdir.
Use Python standard library unless this specific review needs Node standard
library. Include meaningful automated positive/negative/concurrent tests,
README/API usage, alternatives, failure modes, limitations and real evidence.
Aim for at least 12 meaningful test cases and rerun after fixing real failures.
Never claim to have run a test unless job_status completed with returncode=0
and job_output confirms the result.

## Scoped, safe Local Workspace work
Read and write only within: {area}
You may also create exactly one separate completion report at the path
provided by the browser-launch prompt.
For review, source from web-companion may be read, not edited. For other roles,
read other code only as essential to your model, not to modify it.
Do not alter live browser extension, bridge, credentials, SQLite, queue,
another agent's files, Git, installed software or accounts. No browser
tab manipulation, no new agents, no child tasks, no external installs.

## Artifacts
Source implementation, executable meaningful unit/negative tests,
README with architecture and risks; document real tested scenarios,
returncodes, file SHA-256 and unresolved limitations. If a blocker exists,
explain it accurately rather than fabricate PASS.

## Report
When complete, write ONLY the unique launcher-specified
agent-reports/completion-JOB_UUID.md file, containing on separate lines:
Job ID: JOB_UUID
## Outcome
## Evidence
## Tests
## Limitations

This is a bounded browser-recovery regression experiment; report ACK is only
file integrity evidence and not independent model-compute-time evidence.
"""


def queue_job(role, goal, jid):
    import enqueue_completion as ec
    p = BRIEFS / (role + ".md")
    if p.is_symlink():
        raise TrialError("unsafe_brief_symlink")
    body = brief(role, goal)
    BRIEFS.mkdir(parents=True, exist_ok=True)
    if p.exists():
        if p.read_text(encoding="utf-8") != body:
            raise TrialError("immutable_brief_conflict")
    else:
        with p.open("x", encoding="utf-8") as f:
            f.write(body)
    WORK.joinpath(role).mkdir(parents=True, exist_ok=True)
    for folder in (ACTIVE, DONE):
        if (folder / (jid + ".json")).exists():
            raise TrialError("already_claimed_or_done")
    src = QUEUE / (jid + ".json")
    if src.exists() or src.is_symlink():
        if src.is_symlink():
            raise TrialError("unsafe_queue_symlink")
        prior = json.loads(src.read_text(encoding="utf-8"))
        if (prior.get("id") != jid or prior.get("role") != role
                or prior.get("completion_mode") != "report"
                or prior.get("report_name") != report_name(jid)):
            raise TrialError("existing_trial_conflict")
        return src
    QUEUE.mkdir(parents=True, exist_ok=True)
    # Generate identical prompt semantics to the production uploader, but into
    # the isolated queue. Preserve main ACTIVE/DONE anti-replay checks.
    original = ec.QUEUE
    ec.QUEUE = QUEUE
    try:
        metadata = {"campaign_id": "repeat-v064", "ticket_id":role,
                    "stage": "trial", "manifest_sha256":checksum(
                         (EXPERIMENT + "|v0.6.4").encode("utf-8"))}
        res = ec.enqueue(role, "agent-briefs/" + role + ".md",
                         job_id=jid, metadata=metadata)
    finally:
        ec.QUEUE = original
    if res["job_id"] != jid or not src.is_file():
        raise TrialError("job_creation_failed")
    return src


def _read_manifest():
    if not PERMIT.is_file() or PERMIT.is_symlink():
        return None
    try:
        d = json.loads(PERMIT.read_text(encoding="utf-8"))
        allowed = {"schema", "experiment", "version", "created_at",
                   "expires_at", "job_sha256", "job_ids", "max_jobs",
                   "allow_while_legacy_hold"}
        if type(d) is not dict or set(d) != allowed:
            return None
        if (d["schema"] != 1 or d["experiment"] != EXPERIMENT
            or d["version"] != "0.6.4" or d["job_ids"] != list(IDS)
            or d["max_jobs"] != N or d["allow_while_legacy_hold"] is not True
            or type(d["job_sha256"]) is not dict
            or set(d["job_sha256"]) != set(IDS)):
            return None
        if any(not isinstance(v, str) or len(v) != 64 for v in d["job_sha256"].values()):
            return None
        start = datetime.fromisoformat(d["created_at"])
        end = datetime.fromisoformat(d["expires_at"])
        if start.utcoffset() is None or end.utcoffset() is None:
            return None
        if not (0 < (end - start).total_seconds() <= 1200):
            return None
        return d
    except (OSError, TypeError, ValueError, KeyError):
        return None


def prepare():
    """Explicit operator preparation for EXACTLY four authorized jobs."""
    if not HOLD.is_file():
        raise TrialError("legacy_hold_missing")
    if not heartbeat_ready():
        raise TrialError("v064_client_not_ready")
    if PERMIT.exists():
        old = _read_manifest()
        if old:
            return {"ok": True, "existing": True, "job_ids":list(IDS),
                    "queued":sum((QUEUE / (i+".json")).is_file() for i in IDS)}
        raise TrialError("unsafe_or_expired_permit_no_auto_restart")
    if any(p.is_symlink() for p in (PERMIT, LANE, QUEUE)):
        raise TrialError("unsafe_experiment_path")
    if any((ACTIVE/(jid+".json")).exists() or (DONE/(jid+".json")).exists() for jid in IDS):
        raise TrialError("deterministic_trial_already_started")
    # Don't mistake old jobs for cleared browser tabs; the bridge's hard
    # capacity still counts all 17 old ACTIVE records.
    active = list(ACTIVE.glob("*.json"))
    if len(active) > MAX_ACTIVE-N:
        raise TrialError("not_enough_broker_capacity")
    if not list(LEGACY_QUEUE.glob("*.json")):
        raise TrialError("legacy_hold_not_needed")
    QUEUE.mkdir(parents=True, exist_ok=True)
    files = [queue_job(role, goal, jid) for role,goal,jid in zip(ROLES,GOALS,IDS)]
    hashes = {jid:checksum(path.read_bytes()) for jid,path in zip(IDS,files)}
    now = utcnow()
    marker = {"schema":1, "experiment":EXPERIMENT, "version":"0.6.4",
              "created_at":now.isoformat(),
              "expires_at":(now + timedelta(minutes=15)).isoformat(),
              "job_ids":list(IDS), "job_sha256":hashes, "max_jobs":N,
              "allow_while_legacy_hold":True}
    with PERMIT.open("x", encoding="utf-8") as f:
        json.dump(marker, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    return {"ok":True, "prepared":N, "job_ids":list(IDS),
            "expires_at":marker["expires_at"],
            "legacy_queue_untouched":True,"admission_hold_untouched":True}


def claim_held_trial():
    """Must be called under the existing bridge.LOCK; main hold remains on.

    No effect if permit missing/expired, if client incompatible, if cap full,
    if a file mutated, or if the UUID exists in ACTIVE/DONE.
    """
    d = _read_manifest()
    if d is None or not HOLD.is_file() or not heartbeat_ready():
        return None
    if not datetime.fromisoformat(d["created_at"]) <= utcnow() <= datetime.fromisoformat(d["expires_at"]):
        return None
    if len(list(ACTIVE.glob("*.json"))) >= MAX_ACTIVE:
        return None
    for jid in IDS:
        q = QUEUE / (jid + ".json")
        if not q.is_file() or q.is_symlink():
            continue
        if (ACTIVE / (jid+".json")).exists() or (DONE / (jid+".json")).exists():
            continue
        raw = q.read_bytes()
        if checksum(raw) != d["job_sha256"][jid]:
            continue
        entry = json.loads(raw)
        if (entry.get("id") != jid or entry.get("completion_mode") != "report"
            or entry.get("role") != ROLES[IDS.index(jid)]
            or entry.get("report_name") != report_name(jid)
            or entry.get("role_metadata",{}).get("campaign_id") != "repeat-v064"):
            continue
        os.replace(q, ACTIVE / (jid + ".json"))
        return entry
    return None


def preview():
    d = _read_manifest()
    states = {
        "pending":sum((QUEUE / (x+".json")).exists() for x in IDS),
        "active":sum((ACTIVE / (x+".json")).exists() for x in IDS),
        "done":sum((DONE / (x+".json")).exists() for x in IDS)}
    return {"ok":True,"experiment":EXPERIMENT,"permit_valid":d is not None,
            "permit_active":bool(d and utcnow() < datetime.fromisoformat(d["expires_at"])),
            "v064_heartbeat":heartbeat_ready(),
            "legacy_hold":HOLD.is_file(),
            "legacy_active":len(list(ACTIVE.glob("*.json")))-states["active"],
            "legacy_queued":len(list(LEGACY_QUEUE.glob("*.json"))),
            "trial":states,"max_jobs":N,"job_ids":list(IDS)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("action", choices=("prepare","preview"))
    args=p.parse_args()
    try:
        result=prepare() if args.action=="prepare" else preview()
    except (OSError,TypeError,ValueError,TrialError) as e:
        result={"ok":False,"error":str(e)}
    print(json.dumps(result,ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(2)


if __name__=="__main__":main()
