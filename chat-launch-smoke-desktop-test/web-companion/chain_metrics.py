"""Read-only elapsed time and successor-lineage metrics for two-role campaign."""
import json,os,hashlib
from pathlib import Path
from datetime import datetime,timezone
from statistics import median
import chain_campaign as chain

def timestamp(x):
    if not isinstance(x,str):return None
    try:
        d=datetime.fromisoformat(x.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo is not None else None
    except (ValueError,TypeError):return None

def filesystem_birth(path):
    if not path.is_file():return None
    stat=path.stat()
    epoch=getattr(stat,"st_birthtime",None)
    if epoch is None: epoch=stat.st_ctime if os.name=="nt" else stat.st_mtime
    return datetime.fromtimestamp(epoch,timezone.utc)

def difference(a,b):
    return round((b-a).total_seconds(),1) if a and b and b>=a else None

def collect():
    cfg,tickets,digest=chain.load()
    rows=[]
    for ticket in tickets:
        for role in chain.ROLES:
            id=chain.jid(ticket["id"],role)
            state,obj=chain.locate(id)
            p=chain.STATE/"diagnostics"/(id+".json")
            try:events=json.loads(p.read_text(encoding="utf-8"))
            except (OSError,ValueError):events=[]
            def event(stage):
                values=[timestamp(x.get("at")) for x in events
                        if type(x) is dict and x.get("stage")==stage]
                values=[x for x in values if x]
                return min(values) if values else None
            sent=event("send_clicked")
            report=chain.REPORTS/("completion-"+id+".md")
            handoff=chain.handoff_path(ticket["id"],role)
            opened=event("tab_opening")
            receipt=event("receipt_accepted")
            rows.append({
                "ticket":ticket["id"],"role":role,"job_id":id,
                "state":state or "not_dispatched",
                "observed_send_to_report_seconds":difference(sent,filesystem_birth(report)),
                "observed_send_to_handoff_seconds":difference(sent,filesystem_birth(handoff)),
                "opened_to_send_seconds":difference(opened,sent),
                "report_to_receipt_seconds":difference(filesystem_birth(report),receipt),
                "handoff_request_verified":chain.request_valid(ticket["id"],role,digest,tickets,cfg["seed_count"]),
                "server_completed":state=="done" and obj.get("status")=="done" if obj else False,
                "model_compute_seconds":None,
            })
    roles={}
    for role in chain.ROLES:
        subset=[r for r in rows if r["role"]==role]
        values=[r["observed_send_to_report_seconds"] for r in subset
                if r["server_completed"] and r["observed_send_to_report_seconds"] is not None]
        roles[role]={
            "total":len(subset),
            "active":sum(x["state"]=="active" for x in subset),
            "queued":sum(x["state"]=="queue" for x in subset),
            "completed":sum(x["server_completed"] for x in subset),
            "handoffs":sum(x["handoff_request_verified"] for x in subset),
            "median_send_to_report_seconds":round(median(values),1) if values else None
        }
    return {"ok":True,"campaign":"chain2","asof_utc":datetime.now(timezone.utc).isoformat(),
        "roles":roles,"jobs":rows,"note":"Wall clock only; not active model compute time"}

if __name__=="__main__":print(json.dumps(collect(),ensure_ascii=False,indent=2))
