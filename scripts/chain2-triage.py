"""Read-only Chain2 active-job triage.

Never enqueues, replays unknown ChatGPT sends, repairs handoffs, mutates
queue state or asserts that a browser message was actually generated.

Usage: python scripts/chain2-triage.py [--root PATH]
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
ALERTS={"unknown","report_disputed","send_ui_diagnostic",
        "auth_context_changed","tab_binding_disputed",
        "tab_identity_unverified"}
UNVERIFIED_REASONS={"unexpected_close","account_context_changed"}


def _lineage(root: Path):
    candidate=root/"chat-launch-smoke-desktop-test"/"web-companion"/"chain2_lineage_monitor.py"
    if candidate.is_symlink() or not candidate.is_file():
        return {"ok":False,"reason":"lineage_monitor_missing"}
    try:
        result=subprocess.run([sys.executable,str(candidate)],
            cwd=str(candidate.parent),capture_output=True,text=True,
            encoding="utf-8",errors="replace",stdin=subprocess.DEVNULL,timeout=15)
        if result.returncode:
            return {"ok":False,"reason":"lineage_monitor_failed"}
        doc=json.loads(result.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"ok":False,"reason":"lineage_monitor_invalid_output"}
    if (type(doc) is not dict or doc.get("ok") is not True
        or type(doc.get("lineage")) is not list
        or type(doc.get("summary")) is not dict
        or len(doc["lineage"])>100
        or doc.get("no_mutations_performed") is not True):
        return {"ok":False,"reason":"lineage_monitor_untrusted_shape"}
    return doc


def classify(doc):
    if type(doc) is not dict or doc.get("ok") is not True:
        return {"ok":False,"read_only":True,"reason":"invalid_monitor"}
    if (type(doc.get("lineage")) is not list
        or type(doc.get("summary")) is not dict
        or type(doc.get("total_stages")) is not int
        or not 0<=doc["total_stages"]<=100):
        return {"ok":False,"read_only":True,"reason":"invalid_lineage_shape"}
    parents={}
    active=[]
    seen_active=set()
    for row in doc["lineage"]:
        if not isinstance(row,dict):return {"ok":False,"read_only":True,"reason":"invalid_lineage_row"}
        child=row.get("successor_job_id")
        if row.get("state") in ("done","active","queue") and isinstance(child,str):
            if child in parents:return {"ok":False,"read_only":True,"reason":"duplicate_parent"}
            parents[child]=row
        if row.get("state")=="active":
            job_id=row.get("job_id")
            if type(job_id) is not str or job_id in seen_active:
                return {"ok":False,"read_only":True,"reason":"duplicate_or_invalid_active_id"}
            seen_active.add(job_id)
            active.append(row)
    if len(active)!=doc["summary"].get("active"):
        return {"ok":False,"read_only":True,"reason":"active_count_mismatch"}
    groups={}
    incidents=[]
    rows=[]
    for job in active:
        jid=job.get("job_id")
        if not isinstance(jid,str) or len(jid)!=36:
            return {"ok":False,"read_only":True,"reason":"invalid_active_id"}
        parent=parents.get(jid,{})
        raw_alerts=parent.get("successor_incidents",[])
        if not isinstance(raw_alerts,list):raw_alerts=[]
        alerts=[x for x in raw_alerts if isinstance(x,dict)
                and (x.get("stage") in ALERTS or x.get("reason") in UNVERIFIED_REASONS)]
        saved=parent.get("successor_message_saved") is True
        handoff=job.get("handoff_exists") is True
        valid=job.get("handoff_valid") is True
        invalid=handoff and job.get("handoff_valid") is False
        request_status=job.get("request_valid")
        # No automation can infer a missing browser send from silence.
        if alerts or (parent and not saved):
            category="browser_delivery_uncertain"
            action="Review the original chat/session and evidence; do NOT resend automatically"
        elif invalid:
            category="handoff_invalid"
            action="Repair or independently verify the handoff artifact; do not mint a successor"
        elif handoff and valid and request_status is False:
            category="handoff_ready_for_role_review"
            action="Parent role verifies handoff and requests successor in its authorized turn"
        elif handoff and valid and request_status is True:
            category="handoff_request_pending_ack"
            action="Check report ACK/DONE; never duplicate the immutable handoff request"
        else:
            category="worker_evidence_incomplete"
            action="Resume authorized worker and gather tested artifact evidence"
        groups[category]=groups.get(category,0)+1
        if alerts:incidents.append(jid)
        rows.append({
            "job_id":jid,
            "ticket":str(job.get("ticket",""))[:80],
            "role":str(job.get("role",""))[:16],
            "category":category,
            "browser_saved_confirmed":saved,
            "incident_recorded":bool(alerts),
            "handoff_exists":handoff,
            "handoff_valid":job.get("handoff_valid") if handoff else None,
            "handoff_request_valid":handoff and valid and request_status is True,
            "action":action,
        })
    rows.sort(key=lambda x:(x["category"],x["ticket"],x["job_id"]))
    return {
        "ok":True,"read_only":True,"campaign":"chain2",
        "summary":{
            "total_stages":doc["total_stages"],
            "done":doc["summary"].get("done"),
            "active":len(active),
            "queued":doc["summary"].get("queue"),
            "not_started":doc["summary"].get("not_started"),
            "done_with_verified_report_ack":doc["summary"].get("done_ack_verified"),
            "active_categories":groups,
            "active_with_browser_incidents":len(incidents),
        },
        "jobs":rows,"no_jobs_claimed_or_replayed":True,
        "owner_action_required_for_ambiguous_sends":True,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT)
    args=parser.parse_args()
    result=classify(_lineage(args.root.resolve()))
    print(json.dumps(result,ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__=="__main__":
    raise SystemExit(main())
