"""Read-only Chain2 resume gate; never clears browser pause or HOLD."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"bridge-state"
PAUSE=STATE/"admission-hold.json"
EXPECTED_JOB="661a7381-5009-5731-9900-7c747dbe40d2"

def record(name):
    path=STATE/"diagnostics"/name
    if not path.is_file() or path.is_symlink() or path.stat().st_size>65536:
        return None
    try:
        doc=json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc,dict) else None
    except (OSError,ValueError,UnicodeError):
        return None

def age_seconds(doc):
    if not isinstance(doc,dict) or not isinstance(doc.get("at"),str):
        return None
    try:
        d=datetime.fromisoformat(doc["at"].replace("Z","+00:00"))
        if d.tzinfo is None:return None
        return round((datetime.now(timezone.utc)-d.astimezone(timezone.utc)).total_seconds(),1)
    except ValueError:
        return None

def main():
    hold=None
    if PAUSE.is_file() and not PAUSE.is_symlink():
        try:hold=json.loads(PAUSE.read_text(encoding="utf-8"))
        except (OSError,ValueError):pass
    ext=record("extension.json")
    scan=record("recovery-scan.json")
    ext_age=age_seconds(ext)
    scan_age=age_seconds(scan)
    flags={
        "hold_expected":isinstance(hold,dict) and hold.get("reason")=="account_context_changed"
                       and hold.get("affected_job_id")==EXPECTED_JOB,
        "extension_polling":isinstance(ext,dict) and ext.get("state")=="polling"
                             and ext.get("version")=="0.6.6",
        "heartbeat_fresh":ext_age is not None and 0<=ext_age<=30,
        "scan_fresh":scan_age is not None and 0<=scan_age<=30,
        "no_binding_conflicts":isinstance(scan,dict) and scan.get("stored_conflict")==0
                               and scan.get("ambiguous")==0
    }
    print(json.dumps({
        "ok":True,"mode":"READ_ONLY","safe_to_consider_owner_reconciliation":all(flags.values()),
        "flags":flags,"extension_state":ext.get("state") if isinstance(ext,dict) else None,
        "heartbeat_age_seconds":ext_age,"scan_age_seconds":scan_age,
        "hold_present":PAUSE.exists(),
        "must_verify_browser_account_manually":True,
        "no_jobs_claimed_or_replayed":True},ensure_ascii=False))

if __name__=="__main__":
    main()
