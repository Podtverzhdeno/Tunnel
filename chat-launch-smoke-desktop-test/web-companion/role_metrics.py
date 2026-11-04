"""Read-only per-role agent wall-clock and evidence metrics for role campaigns.

Do not confuse report latency or browser tab lifetime with LLM compute time.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from statistics import median
import role_campaign as rc

def time_at(s):
    if type(s) is not str:return None
    try:
        t=datetime.fromisoformat(s.replace("Z","+00:00"))
        return t.astimezone(timezone.utc) if t.utcoffset() is not None else None
    except (ValueError,TypeError):return None

def earliest(events,stage):
    values=[time_at(e.get("at")) for e in events
            if type(e) is dict and e.get("stage")==stage]
    values=[x for x in values if x is not None]
    return min(values) if values else None

def duration(start,stop):
    if start is None or stop is None or stop<start:return None
    return round((stop-start).total_seconds(),1)

def file_birth(path):
    if not path.is_file():return None
    s=path.stat()
    v=getattr(s,"st_birthtime",None)
    if v is None:
        v=s.st_ctime if os.name=="nt" else s.st_mtime
    return datetime.fromtimestamp(v,timezone.utc)

def collect(cid="eng1"):
    data,sha=rc.load(cid)
    rows=[]
    now=datetime.now(timezone.utc)
    for t in data["tickets"]:
        for stage in rc.PHASES:
            jid=rc.stage_id(cid,t["id"],stage)
            state,job=rc.lookup(jid)
            path=rc.STATE/"diagnostics"/(jid+".json")
            try:
                events=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
                if type(events) is not list:events=[]
            except (OSError,ValueError):
                events=[]
            opened=earliest(events,"tab_opening")
            sent=earliest(events,"send_clicked")
            saved=earliest(events,"message_saved")
            acknowledged=earliest(events,"report_acked")
            finalized=earliest(events,"receipt_accepted")
            report=rc.REPORTS/("completion-"+jid+".md")
            created=file_birth(report)
            report_sha=hashlib.sha256(report.read_bytes()).hexdigest() if report.is_file() else None
            ack_path=rc.ACKS/(jid+".json")
            try:
                ack=json.loads(ack_path.read_text(encoding="utf-8")) if ack_path.is_file() else {}
            except (ValueError,OSError):
                ack={}
            matched=bool(ack.get("job_id")==jid and ack.get("status")=="REPORT_ACKED"
                         and report_sha and ack.get("artifact_sha256")==report_sha)
            state_str=state or ("ready" if rc.PREVIOUS[stage] is None or
                rc.accepted(cid,t["id"],rc.PREVIOUS[stage],sha) else "blocked")
            oldest=time_at(job.get("created")) if job else None
            latest=max((time_at(x.get("at")) for x in events if type(x) is dict
                        and time_at(x.get("at"))),default=None)
            rows.append({
              "ticket":t["id"],"role":stage,"job_id":jid,
              "state":state_str,"report_ack_verified":matched,
              "send_to_report_seconds":duration(sent,created),
              "open_to_send_seconds":duration(opened,sent),
              "report_to_finalized_seconds":duration(created,finalized),
              "queued_wait_seconds":duration(oldest,now) if state=="queue" else None,
              "last_event":next((e.get("stage") for e in reversed(events)
                                  if type(e) is dict),None),
              "last_event_age_seconds":duration(latest,now),
              "model_compute_seconds":None,
              "recorded_completed":state=="done" and job.get("status")=="done" if job else False
            })
    stages={}
    for role in rc.PHASES:
        picked=[x for x in rows if x["role"]==role]
        values=[x["send_to_report_seconds"] for x in picked
                if x["report_ack_verified"] and x["send_to_report_seconds"] is not None]
        stages[role]={
           "total":len(picked),"queued":sum(x["state"]=="queue" for x in picked),
           "active":sum(x["state"]=="active" for x in picked),
           "completed":sum(x["recorded_completed"] for x in picked),
           "verified_reports":sum(x["report_ack_verified"] for x in picked),
           "median_send_to_report_seconds":round(median(values),1) if values else None
        }
    return {"ok":True,"campaign":cid,"asof_utc":now.isoformat(timespec="seconds"),
            "total":len(rows),"per_role":stages,"tasks":rows,
            "note":"Durations are observed wall-clock, not model inference/continuous reasoning."}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--campaign",default="eng1")
    args=ap.parse_args()
    print(json.dumps(collect(args.campaign),ensure_ascii=False,indent=2))

if __name__=="__main__": main()
