"""One-time, owner-foreground, fail-closed archival of held browser job records.

Never marks an uncertain ChatGPT response DONE and never retries a prompt.
Requires a fresh browser scan with zero owned jobs, a SHA-bound preview, and
a REAL interactive Windows-terminal confirmation. MCP cannot apply this.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from legacy_hold_audit import snapshot as legacy_snapshot
from repeat_v064 import IDS as REPEAT_IDS
from enqueue_completion import enqueue

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "bridge-state"
HOLD = STATE / "admission-hold.json"
ARCHIVE = STATE / "owner-reconciliation" / "backlog-unblock-20261103"
EXPECTED = {"held_no_dispatch": 8, "manual_browser_tab_not_found": 12,
            "manual_uncertain_result": 5}
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")
LIMIT = 1024 * 1024
NEW_PLAN = ROOT / "backlog-unblock-20261103-plan.json"


def staged_agents():
    doc = read_obj(NEW_PLAN)
    if (doc.get("schema") != "backlog-fanout-plan-v1" or
        doc.get("campaign") != "backlog-unblock-20261103" or
        doc.get("max_agents") != 10 or doc.get("descendant_budget") != 0 or
        doc.get("state") != "STAGED_NOT_DISPATCHED" or
        doc.get("server_admission_required") is not True):
        raise RuntimeError("new_campaign_plan_invalid")
    items = doc.get("agents")
    if type(items) is not list or len(items) != 10:
        raise RuntimeError("new_campaign_count_invalid")
    seen = set()
    validated = []
    for x in items:
        role, brief = x.get("role"), x.get("brief")
        if (type(role) is not str or not re.fullmatch(r"backlog-[0-9]{2}-[a-z0-9-]+", role)
            or role in seen or brief != "agent-briefs/" + role + ".md"
            or x.get("mode") != "completion-gated"
            or x.get("report") != "agent-reports/completion-JOBID.md"):
            raise RuntimeError("new_campaign_role_invalid")
        seen.add(role)
        if sha(ROOT.parent / brief) != x.get("brief_sha256"):
            raise RuntimeError("new_campaign_brief_drift:" + role)
        validated.append((role, brief))
    return validated, sha(NEW_PLAN)


def sha(path: Path):
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= LIMIT:
        raise RuntimeError("missing_or_unsafe_file:" + path.name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_obj(path: Path):
    sha(path)
    item = json.loads(path.read_text(encoding="utf-8"))
    if type(item) is not dict:
        raise RuntimeError("invalid_object:" + path.name)
    return item


def fresh_zero_owned():
    hb = read_obj(STATE / "diagnostics" / "extension.json")
    scan = read_obj(STATE / "diagnostics" / "recovery-scan.json")
    now = datetime.now(timezone.utc)
    for item in (hb, scan):
        t = datetime.fromisoformat(item["at"].replace("Z", "+00:00"))
        age = (now - t).total_seconds()
        if not 0 <= age <= 120:
            raise RuntimeError("browser_heartbeat_or_scan_not_fresh")
    if hb.get("version") != "0.6.5" or hb.get("state") != "polling":
        raise RuntimeError("browser_client_not_ready")
    if any(type(scan.get(k)) is not int or scan[k] != 0
           for k in ("identified", "eligible", "recovered", "ambiguous", "stored_conflict")):
        raise RuntimeError("browser_owned_or_conflicting_tabs")
    return {"heartbeat_at": hb["at"], "scan_at": scan["at"],
            "unresolved_browser_bindings": scan.get("stored_owned")}


def checked_job(state, jid, decision, *, require_ack=False, require_unexpected=False):
    if not UUID.fullmatch(jid):
        raise RuntimeError("invalid_job_id")
    source = STATE / state / (jid + ".json")
    doc = read_obj(source)
    if doc.get("id") != jid:
        raise RuntimeError("job_id_mismatch")
    if (STATE / "done" / (jid + ".json")).exists():
        raise RuntimeError("overlapping_DONE:" + jid)
    events_path = STATE / "diagnostics" / (jid + ".json")
    events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.is_file() else []
    if type(events) is not list or any(type(e) is not dict for e in events):
        raise RuntimeError("invalid_diagnostics:" + jid)
    if state == "queue" and any(e.get("stage") in ("send_clicked", "message_saved")
                                for e in events):
        raise RuntimeError("queued_job_has_send_evidence:" + jid)
    if require_unexpected and not any(e.get("stage") == "tab_closed"
           and e.get("reason") == "unexpected_close" for e in events):
        raise RuntimeError("unexpected_close_unverified:" + jid)
    report = ROOT.parent / "agent-reports" / ("completion-" + jid + ".md")
    ackfile = STATE / "completion-acks" / (jid + ".json")
    evidence = {"report_sha256": None, "ack_sha256": None}
    if require_ack or ackfile.exists():
        receipt = read_obj(ackfile)
        report_sha = sha(report)
        if (receipt.get("job_id") != jid or receipt.get("status") != "REPORT_ACKED"
            or receipt.get("artifact_sha256") != report_sha):
            raise RuntimeError("report_ack_not_verified:" + jid)
        evidence = {"report_sha256": report_sha, "ack_sha256": sha(ackfile)}
    elif report.exists():
        # Never silently quarantine an unacknowledged report.
        raise RuntimeError("unacked_report:" + jid)
    if state == "queue" and ackfile.exists():
        raise RuntimeError("queued_job_has_ack:" + jid)
    return {"id": jid, "source_state": state, "classification": decision,
            "job_sha256": sha(source), **evidence}


def plan():
    sha(HOLD)
    agents, dispatch_sha = staged_agents()
    browser = fresh_zero_owned()
    legacy = legacy_snapshot()
    if legacy.get("ok") is not True or legacy.get("hold_present") is not True:
        raise RuntimeError("legacy_snapshot_invalid")
    if legacy.get("decisions") != EXPECTED:
        raise RuntimeError("legacy_counts_or_classes_changed")
    counts = legacy.get("counts", {})
    if (counts.get("active") != 17 or counts.get("queued") != 8
        or counts.get("state_conflicts") != 0 or counts.get("acks_matching") != 12
        or not legacy.get("fresh_scan_zero_agents")):
        raise RuntimeError("legacy_preconditions_not_met")
    jobs = []
    for x in legacy["agents"]:
        decision, state, jid = x["decision"], x["state"], x["job_id"]
        if decision not in EXPECTED:
            raise RuntimeError("unreviewed_legacy_classification")
        jobs.append(checked_job(state, jid, decision,
                                require_ack=(decision == "manual_browser_tab_not_found")))
    for jid in sorted(REPEAT_IDS):
        jobs.append(checked_job("active", jid, "manual_unexpected_close",
                                require_ack=True, require_unexpected=True))
    if len(jobs) != 29 or len(set(x["id"] for x in jobs)) != 29:
        raise RuntimeError("missing_or_duplicate_jobs")
    actual = {p.stem for state in ("queue", "active")
              for p in (STATE / state).glob("*.json")}
    if actual != {x["id"] for x in jobs}:
        raise RuntimeError("live_state_contains_unclassified_jobs")
    jobs.sort(key=lambda x: (x["source_state"], x["id"]))
    manifest = {"schema": "owner-held-reconciliation-v1",
                "status": "PREVIEW_ONLY",
                "policy": "QUARANTINE_NOT_DONE_NO_REPLAY",
                "host_actor_authenticated": False,
                "hold_sha256": sha(HOLD),
                "new_dispatch_plan_sha256": dispatch_sha,
                "new_roles": [role for role, _ in agents],
                "jobs": jobs}
    raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    plan_id = hashlib.sha256(raw).hexdigest()
    return manifest, plan_id, browser


def apply(manifest, plan_id):
    if os.name != "nt" or not sys.stdin.isatty():
        raise RuntimeError("FOREGROUND_WINDOWS_TERMINAL_REQUIRED")
    response = input("Type ARCHIVE " + plan_id[:16] + " to quarantine 29 old jobs: ").strip()
    if response != "ARCHIVE " + plan_id[:16]:
        raise RuntimeError("OWNER_CONFIRMATION_MISMATCH")
    if ARCHIVE.exists():
        raise RuntimeError("archive_already_exists_stop_manual_reconciliation")
    # No writes occur before all evidence has been verified.
    ARCHIVE.mkdir(parents=True, exist_ok=False)
    staged = ARCHIVE / "staged-manifest.json"
    staged.write_text(json.dumps({**manifest, "status": "IN_PROGRESS",
        "plan_sha256": plan_id}, ensure_ascii=False, indent=2), encoding="utf-8")
    moved = []
    new_ids = []
    released = False
    try:
        for item in manifest["jobs"]:
            state, jid = item["source_state"], item["id"]
            src = STATE / state / (jid + ".json")
            if sha(src) != item["job_sha256"]:
                raise RuntimeError("source_drift:" + jid)
            targetdir = ARCHIVE / state
            targetdir.mkdir(exist_ok=True)
            target = targetdir / src.name
            if target.exists():
                raise RuntimeError("archive_conflict")
            os.replace(src, target)
            moved.append((src, target))
        if any(any((STATE / s).glob("*.json")) for s in ("queue", "active")):
            raise RuntimeError("new_jobs_appeared_keep_hold")
        agents, dispatch_sha = staged_agents()
        if (dispatch_sha != manifest["new_dispatch_plan_sha256"] or
                [role for role, _ in agents] != manifest["new_roles"]):
            raise RuntimeError("new_dispatch_plan_drift")
        for role, brief in agents:
            result = enqueue(role, brief)
            if not result.get("queued") or not result.get("job_id"):
                raise RuntimeError("failed_to_stage_new_agent")
            new_ids.append(result["job_id"])
        staged = {p.stem for p in (STATE / "queue").glob("*.json")}
        if staged != set(new_ids) or any((STATE / "active").glob("*.json")):
            raise RuntimeError("staged_job_set_mismatch")
        fresh_zero_owned()
        if sha(HOLD) != manifest["hold_sha256"]:
            raise RuntimeError("hold_drift_keep_hold")
        report = {**manifest, "status": "READY_TO_RELEASE",
                  "plan_sha256": plan_id, "new_agent_job_ids": new_ids,
                  "new_jobs_staged": len(new_ids), "old_jobs_quarantined": len(moved),
                  "hold_released": False, "server_done_claimed": 0,
                  "prompts_retried": 0,
                  "committed_at": datetime.now(timezone.utc).isoformat()}
        (ARCHIVE / "receipt.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Commit point: the browser may now claim ONLY the ten new jobs.
        os.replace(HOLD, ARCHIVE / "admission-hold.json")
        released = True
        try:
            report["status"] = "QUARANTINED_UNRESOLVED_NEW_JOBS_RELEASED"
            report["hold_released"] = True
            tmp = ARCHIVE / "receipt.writing"
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, ARCHIVE / "receipt.json")
        except OSError:
            pass  # Commit already happened; never roll back a potentially sent job.
        return {"ok": True, "status": report["status"], "archived": len(moved),
                "queued_new": len(new_ids), "new_job_ids": new_ids,
                "hold_released": True, "done_claimed": 0, "retries": 0}
    except Exception:
        # Never roll back after release: ChatGPT may already have received a job.
        if released:
            raise
        # With hold intact no browser can claim these staged jobs.
        for jid in reversed(new_ids):
            staged_path = STATE / "queue" / (jid + ".json")
            if staged_path.exists():
                (ARCHIVE / "aborted-new").mkdir(exist_ok=True)
                os.replace(staged_path, ARCHIVE / "aborted-new" / staged_path.name)
        for src, target in reversed(moved):
            if target.exists() and not src.exists():
                os.replace(target, src)
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-id")
    args = ap.parse_args()
    manifest, pid, browser = plan()
    if not args.apply:
        result = {"ok": True, "mode": "READ_ONLY_PREVIEW",
                  "plan_sha256": pid, "jobs": len(manifest["jobs"]),
                  "classifications": {k: sum(x["classification"] == k for x in
                        manifest["jobs"]) for k in
                        ("held_no_dispatch", "manual_browser_tab_not_found",
                         "manual_uncertain_result", "manual_unexpected_close")},
                  "browser": browser, "hold_released": False,
                  "next": "Run in real local Windows terminal with --apply --plan-id, then verify receipt"}
    else:
        if not args.plan_id or args.plan_id != pid:
            raise RuntimeError("PLAN_DIGEST_CHANGED")
        result = apply(manifest, pid)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
