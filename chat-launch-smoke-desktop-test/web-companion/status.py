"""Read-only self-diagnostics for ChatGPT background launcher.
Outputs job IDs, stages, result URLs and error classes, never prompts or keys.
Use via Local Workspace MCP run_script; no DevTools or clipboard needed.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent/"bridge-state"
def summary(label,p):
    job={"id":p.stem,"state":label}
    if label in ("queue","active","done"):
        d=json.loads(p.read_text(encoding="utf-8"))
        if d.get("completion_mode")=="report" or d.get("report_ack_id"):
            job["completion_mode"]="report"
        if label=="done":
            job.update(state=d["status"],url=d.get("url",""))
            if d.get("report_ack_id"):
                job["report_ack_id"]=d["report_ack_id"]
                job["report_sha256"]=d.get("report_sha256")
        elif d.get("completion_mode")=="report":
            ack=ROOT/"completion-acks"/(p.stem+".json")
            job["report_ack_recorded"]=ack.is_file()
            report=ROOT.parent.parent/"agent-reports"/("completion-"+p.stem+".md")
            job["report_exists"]=report.is_file()
    telemetry=ROOT/"diagnostics"/(p.stem+".json")
    if telemetry.is_file():
        try:
            events=json.loads(telemetry.read_text(encoding="utf-8"))
            if isinstance(events,list) and events:
                # Keep only bounded telemetry labels/time, no prompt, tokens or transcript.
                safe=[{"at":e.get("at",""),"stage":e.get("stage",""),
                       **({"reason":e["reason"]} if e.get("reason") else {})}
                      for e in events[-20:] if isinstance(e,dict)]
                job["last_stage"]=safe[-1]["stage"] if safe else None
                job["last_reason"]=next((e["reason"] for e in reversed(safe)
                                         if e.get("reason")),None)
                job["events"]=safe
                job["event_count"]=len(events)
        except (OSError,ValueError,TypeError):
            job["telemetry_error"]="invalid_telemetry_file"
    return job

def find_latest():
    states=[]
    for label in ("queue","active","done"):
        for p in (ROOT/label).glob("*.json"):
            states.append((p.stat().st_mtime,label,p))
    states.sort(reverse=True)
    out=[summary(label,p) for _,label,p in states[:10]]
    ext_path=ROOT/"diagnostics"/"extension.json"
    extension=None
    if ext_path.is_file():
        try:
            ext=json.loads(ext_path.read_text(encoding="utf-8"))
            last=datetime.fromisoformat(ext["at"])
            extension={
                "last_seen":ext["at"],
                "age_seconds":max(0,int((datetime.now(timezone.utc)-last).total_seconds())),
                "version":ext.get("version"),
                "state":ext.get("state"),
                "window_count":ext.get("windows")
            }
        except (ValueError,KeyError,TypeError,OSError):
            extension={"status":"unreadable"}
    recovery=None
    scan_file=ROOT/"diagnostics"/"recovery-scan.json"
    if scan_file.is_file():
        try:
            raw=json.loads(scan_file.read_text(encoding="utf-8"))
            fields=("tabs_total","chat_tabs","non_chatgpt_tabs",
                    "identified","eligible","recovered","ambiguous",
                    "stored_owned","stored_invalid","stored_conflict",
                    "restored_from_local","recovered_dom_only")
            recovery={"at":raw.get("at")}
            for field in fields:
                value=raw.get(field,0)
                if type(value) is int and 0 <= value <= 1000:
                    recovery[field]=value
        except (OSError,ValueError,TypeError):
            recovery={"status":"unreadable"}
    return {
       "ok":True,
       "diagnostics_version":1,
       "extension":extension,
       "recovery_scan":recovery,
       "jobs":out,
       "latest":out[0] if out else None,
       "note":"Telemetry is local metadata; submitted is not proof of a finished GPT answer."
    }
if __name__=="__main__":
    print(json.dumps(find_latest(),ensure_ascii=False))
