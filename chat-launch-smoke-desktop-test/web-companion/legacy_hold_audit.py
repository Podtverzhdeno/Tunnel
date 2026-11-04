"""Read-only, privacy-minimal triage for held pre-Chain2 browser work.

No queue, ACK, browser tab or result mutations. In particular, this script
never converts an ACK or a tab-closed event into server DONE.
"""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from statistics import median

import bridge
import repeat_v064

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"bridge-state"
REPORTS=ROOT.parent/"agent-reports"
LIMIT=1_048_576


def read_json(path):
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size>LIMIT:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,UnicodeError):
        return None


def valid_id(value):
    if not isinstance(value,str):
        return False
    try:
        return str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):
        return False


def artifact_sha(path):
    try:
        if (not path.is_file() or path.is_symlink()
                or not 0<path.stat().st_size<=LIMIT):
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def file_birth(path):
    try:
        if not path.is_file() or path.is_symlink():
            return None
        stat=path.stat()
        t=getattr(stat,"st_birthtime",None)
        if t is None:t=stat.st_ctime if os.name=="nt" else stat.st_mtime
        return datetime.fromtimestamp(t,timezone.utc)
    except (OSError,ValueError,OverflowError):
        return None


def event_time(raw):
    try:
        if type(raw) is not str:return None
        t=datetime.fromisoformat(raw.replace("Z","+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (TypeError,ValueError,OverflowError):
        return None


def send_to_report_duration(events,report):
    if type(events) is not list:return None
    sends=[event_time(x.get("at")) for x in events
           if type(x) is dict and x.get("stage")=="send_clicked"]
    sends=[s for s in sends if s is not None]
    created=file_birth(report)
    if not sends or created is None:return None
    first=min(sends)
    return round((created-first).total_seconds(),1) if created>=first else None


def fresh_zero_agent_scan():
    """Only a recent aggregate scan with zero identified tabs proves absence now."""
    scan=read_json(STATE/"diagnostics"/"recovery-scan.json")
    if type(scan) is not dict:
        return False
    try:
        observed=datetime.fromisoformat(scan["at"].replace("Z","+00:00"))
        age=(datetime.now(timezone.utc)-observed.astimezone(timezone.utc)).total_seconds()
        return (0<=age<=120 and type(scan.get("identified")) is int
                and scan["identified"]==0 and type(scan.get("eligible")) is int
                and scan["eligible"]==0 and type(scan.get("recovered")) is int
                and scan["recovered"]==0)
    except (ValueError,TypeError,KeyError,AttributeError):
        return False


def triage(*,state,doc,events,report_exists,ack_exists,ack_valid,recoverable,
           fresh_zero_agents=False):
    """Advice only, never an admission, recovery or completion authorization."""
    if state=="conflict":
        return "manual_state_conflict"
    if doc is None:
        return "manual_unreadable_job"
    if state=="queue":
        return "held_no_dispatch"
    if state!="active":
        return "manual_unknown_state"
    if (not isinstance(events,list)
            or any(type(x) is not dict for x in events)):
        return "manual_unreadable_events"
    if any(x.get("stage")=="tab_closed" and
           x.get("reason")=="unexpected_close" for x in events):
        return "manual_unexpected_close"
    if any(x.get("stage") in (
        "unknown","report_disputed","tab_binding_disputed","tab_preserved")
           for x in events):
        return "manual_uncertain_result"
    if report_exists and (ack_exists and not ack_valid):
        return "manual_disputed_ack"
    if ack_exists and not ack_valid:
        return "manual_disputed_ack"
    if ack_valid:
        if not recoverable:
            return "manual_recovery_ineligible"
        return "manual_browser_tab_not_found" if fresh_zero_agents else "await_owned_tab"
    if report_exists:
        return "await_ack_without_replay"
    if any(x.get("stage")=="send_clicked" for x in events):
        return "await_report_without_replay"
    return "manual_presend_unknown"


def snapshot():
    exclude=set(repeat_v064.IDS)
    entries={}
    conflicts=set()
    unsafe_filenames=0
    for state in ("queue","active"):
        directory=STATE/state
        for path in directory.glob("*.json"):
            jid=path.stem
            if not valid_id(jid):
                unsafe_filenames+=1
                continue
            if jid in exclude:
                continue
            if jid in entries:
                conflicts.add(jid)
                continue
            entries[jid]=(state,path)
    # A job listed as ACTIVE and already in DONE is a conflicting state.
    done_dir=STATE/"done"
    for jid in entries:
        if (done_dir/(jid+".json")).exists():
            conflicts.add(jid)
    rows=[]
    browser_scan_zero=fresh_zero_agent_scan()
    for jid,(state,path) in sorted(entries.items()):
        doc=read_json(path)
        if type(doc) is not dict or doc.get("id")!=jid:
            doc=None
        report=REPORTS/("completion-"+jid+".md")
        ack=STATE/"completion-acks"/(jid+".json")
        digest=artifact_sha(report)
        receipt=read_json(ack)
        if type(receipt) is not dict:receipt=None
        ack_valid=bool(
            digest and receipt and receipt.get("job_id")==jid
            and receipt.get("status")=="REPORT_ACKED"
            and receipt.get("artifact_sha256")==digest)
        trace=read_json(STATE/"diagnostics"/(jid+".json"))
        if trace is None and not (STATE/"diagnostics"/(jid+".json")).exists():
            trace=[]
        recovery_ok=False
        recovery_reason="not_checked"
        if state=="active" and jid not in conflicts:
            try:
                recovery_ok,recovery_reason=bridge.recovery_candidate(jid)
            except (OSError,ValueError,TypeError,AttributeError):
                recovery_reason="check_unavailable"
        stages=[e.get("stage") for e in trace if type(e) is dict] if type(trace) is list else []
        duration=(send_to_report_duration(trace,report)
                  if digest is not None else None)
        decision=triage(state="conflict" if jid in conflicts else state,
                        doc=doc,events=trace,report_exists=digest is not None,
                        ack_exists=ack.exists(),ack_valid=ack_valid,
                        recoverable=recovery_ok,fresh_zero_agents=browser_scan_zero)
        rows.append({
            "job_id":jid,"state":"conflict" if jid in conflicts else state,
            "report_integrity_readable":digest is not None,
            "ack_present":ack.exists(),"ack_sha_matches":ack_valid,
            "send_to_report_seconds":duration,
            "model_compute_seconds":None,
            "last_stage":stages[-1] if stages else None,
            "unexpected_close":bool(type(trace) is list and any(
                type(e) is dict and e.get("stage")=="tab_closed"
                and e.get("reason")=="unexpected_close" for e in trace)),
            "recovery_eligible":recovery_ok,"recovery_reason":recovery_reason,
            "decision":decision})
    c=Counter(r["decision"] for r in rows)
    wall=[x["send_to_report_seconds"] for x in rows
          if x["ack_sha_matches"] and x["send_to_report_seconds"] is not None]
    return {
        "ok":True,"scope":"held_legacy_excluding_repeat_v064",
        "hold_present":(STATE/"admission-hold.json").is_file(),
        "fresh_scan_zero_agents":browser_scan_zero,
        "counts":{"total":len(rows),
                  "active":sum(x["state"]=="active" for x in rows),
                  "queued":sum(x["state"]=="queue" for x in rows),
                  "state_conflicts":len(conflicts),
                  "invalid_filenames":unsafe_filenames,
                  "reports_readable":sum(x["report_integrity_readable"] for x in rows),
                  "acks_matching":sum(x["ack_sha_matches"] for x in rows),
                  "timed_reports":sum(
                      x["ack_sha_matches"] and x["send_to_report_seconds"] is not None
                      for x in rows),
                  "unexpected_close":sum(x["unexpected_close"] for x in rows),
                  "recovery_eligible":sum(x["recovery_eligible"] for x in rows)},
        "decisions":dict(sorted(c.items())),
        "median_send_to_report_seconds":round(median(wall),1) if wall else None,
        "agents":rows,
        "note":"Advisory only. No tab identity, model execution, independent report quality or automatic DONE proof."
    }


if __name__=="__main__":
    print(json.dumps(snapshot(),ensure_ascii=False,indent=2))
