"""One-shot bounded 10-chat fanout; no retries, no model API, no recursion.
Must be called through the connected Local Workspace in an authorized turn.
Requires live extension heartbeat version 0.3.0.
"""
import argparse
import json
import os
import re
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"bridge-state"
PLAN=ROOT/"ten_agent_plan.json"
BATCHES=STATE/"batches"
QUEUE=STATE/"queue"
ACTIVE=STATE/"active"
DONE=STATE/"done"
HEARTBEAT=STATE/"diagnostics"/"extension.json"

def planned():
    plan=json.loads(PLAN.read_text(encoding="utf-8"))
    assert isinstance(plan.get("jobs"),list) and len(plan["jobs"])==10
    assert plan["max_total_new_chats"]==10 and plan["max_descendants"]==0
    run=plan["run_id"]
    if not re.fullmatch(r"[a-z0-9-]{8,80}",run):
        raise ValueError("invalid_run_id")
    seen=set()
    items=[]
    for value in plan["jobs"]:
        key=value["id"]
        if not re.fullmatch(r"[a-z0-9-]{3,40}",key) or key in seen:
            raise ValueError("duplicate_or_invalid_agent_role")
        seen.add(key)
        prompt=value["prompt"]
        if not isinstance(prompt,str) or not 150<=len(prompt)<=6000:
            raise ValueError("invalid_prompt_size")
        hold=value.get("minHoldSeconds",120)
        if not isinstance(hold,int) or not 30<=hold<=180:
            raise ValueError("unsafe_hold_duration")
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,run+"/"+key))
        items.append({"id":identifier,"prompt":prompt,"minHoldSeconds":hold,
                      "batch":run,"role":key})
    return run,items

def execute(dry_run=False):
    run,items=planned()
    if dry_run:
        return {"ok":True,"dry_run":True,"batch":run,"roles":[i["role"] for i in items],
                "count":len(items),"max_descendants":0}
    if not HEARTBEAT.is_file():
        raise RuntimeError("No extension heartbeat: reload version 0.3.0 before dispatch")
    ext=json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    if ext.get("version")!="0.3.0":
        raise RuntimeError("Extension version must be 0.3.0; current: "+str(ext.get("version")))
    from datetime import datetime,timezone
    seen=datetime.fromisoformat(ext["at"])
    if (datetime.now(timezone.utc)-seen).total_seconds()>120:
        raise RuntimeError("Extension heartbeat stale; refuse unattended batch")
    for d in (BATCHES,QUEUE,ACTIVE,DONE):
        d.mkdir(parents=True,exist_ok=True)
    marker=BATCHES/(run+".json")
    if marker.exists():
        saved=json.loads(marker.read_text(encoding="utf-8"))
        if saved.get("ids")==[x["id"] for x in items]:
            return {"ok":True,"queued":False,"already_exists":True,
                    "batch":run,"ids":saved["ids"]}
        raise RuntimeError("Batch marker conflict: cannot overwrite")
    if any(QUEUE.glob("*.json")) or any(ACTIVE.glob("*.json")):
        raise RuntimeError("Existing queued or active tasks; refuse to start fanout")
    created=[]
    try:
        for job in items:
            path=QUEUE/(job["id"]+".json")
            with path.open("x",encoding="utf-8") as f:
                json.dump(job,f,ensure_ascii=False)
            created.append(path)
        with marker.open("x",encoding="utf-8") as f:
            json.dump({"batch":run,"ids":[j["id"] for j in items],
                       "roles":[j["role"] for j in items],"count":10},f)
    except Exception:
        # Remove only unclaimed, files created in THIS run; never delete active work.
        for path in created:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        raise
    return {"ok":True,"queued":True,"batch":run,"count":10,
            "ids":[j["id"] for j in items],"descendant_budget":0}

if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--dry-run",action="store_true")
    args=a.parse_args()
    print(json.dumps(execute(args.dry_run),ensure_ascii=False))
