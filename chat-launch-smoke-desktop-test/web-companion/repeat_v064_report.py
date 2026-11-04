"""Read-only evidence snapshot for live v0.6.4 repeat experiment."""
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
import repeat_v064 as t

def parse(s):
    if not isinstance(s,str):return None
    try:
        d=datetime.fromisoformat(s.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo is not None else None
    except (ValueError,TypeError):return None

def created(p):
    if not p.is_file():return None
    s=p.stat()
    v=getattr(s,"st_birthtime",None)
    if v is None:v=s.st_ctime if os.name=="nt" else s.st_mtime
    return datetime.fromtimestamp(v,timezone.utc)

def elapsed(a,b):
    return round((b-a).total_seconds(),1) if a and b and b>=a else None

def safe_json(p):
    try:
        if not p.is_file() or p.is_symlink() or p.stat().st_size>1_048_576:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,ValueError,UnicodeError):
        return None

def saved_after_latest_send(events):
    """Require the latest persisted message to follow the latest send."""
    sent=next((i for i in range(len(events)-1,-1,-1)
               if events[i].get("stage")=="send_clicked"),None)
    saved=next((i for i in range(len(events)-1,-1,-1)
                if events[i].get("stage")=="message_saved"),None)
    return sent is not None and saved is not None and saved>=sent


def reconciliation_decision(*,state,valid_ack,has_report,
                            expected_close,unexpected_close,events,
                            strict_done_receipt=False):
    """Advisory, read-only; never authorizes DONE, retry or browser closure."""
    if state=="queue":
        return "await_dispatch"
    if state=="conflict":
        return "manual_multiple_state_files"
    if state not in ("active","done"):
        return "manual_missing_state"
    if unexpected_close:
        return "manual_unexpected_close"
    if has_report and not valid_ack or valid_ack and not has_report:
        return "manual_report_disputed"
    if any(e.get("stage") in (
        "unknown","runtime_error","report_disputed","tab_preserved",
        "tab_binding_disputed") or e.get("reason")=="account_context_changed"
           for e in events):
        return "manual_identity_or_unknown"
    if not saved_after_latest_send(events):
        return "manual_send_uncertain"
    if state=="done":
        if not valid_ack:
            return "manual_report_disputed"
        if not strict_done_receipt:
            return "manual_receipt_disputed"
        return "local_pipeline_confirmed" if expected_close else "inspect_close_only_eligibility"
    if expected_close:
        return "manual_result_missing"
    return "await_browser_identity" if valid_ack else "await_report_ack"


def snapshot():
    now=datetime.now(timezone.utc)
    rows=[]
    for role,jid in zip(t.ROLES,t.IDS):
        locations=[x for x,root in (("queue",t.QUEUE),("active",t.ACTIVE),("done",t.DONE))
                   if (root/(jid+".json")).exists()]
        state=(locations[0] if len(locations)==1 else
               "conflict" if len(locations)>1 else "missing")
        events=safe_json(t.DIAG/(jid+".json"))
        if not isinstance(events,list):events=[]
        phases=[{"at":e.get("at"),"stage":e.get("stage"),
                 **({"reason":e["reason"]} if e.get("reason") else {})}
                for e in events if type(e) is dict]
        first={}
        for e in phases:
            d=parse(e["at"])
            if d and e["stage"] not in first:
                first[e["stage"]]=d
        report=t.PROJECT/"agent-reports"/("completion-"+jid+".md")
        ack=safe_json(t.STATE/"completion-acks"/(jid+".json"))
        if not isinstance(ack,dict):ack={}
        has_report=report.is_file()
        sha=None
        try:
            if has_report and not report.is_symlink() and report.stat().st_size<=1_048_576:
                sha=hashlib.sha256(report.read_bytes()).hexdigest()
        except OSError:
            pass
        valid=bool(sha and ack.get("status")=="REPORT_ACKED"
                   and ack.get("job_id")==jid and ack.get("artifact_sha256")==sha)
        work=t.WORK/role
        outputs=[]
        if work.is_dir():
            for f in work.rglob("*"):
                if f.is_file() and not f.is_symlink() and len(outputs)<40:
                    outputs.append(str(f.relative_to(work)).replace("\\","/"))
        error=[e for e in phases if e["stage"] in
               ("unknown","report_disputed","tab_binding_disputed",
                "tab_identity_unverified","tab_close_failed",
                "tab_close_skipped","handoff_failed","runtime_error")]
        last_closure=next((e for e in reversed(phases) if e["stage"]=="tab_closed"),None)
        closure_unexpected=any(e["stage"]=="tab_closed" and
                               e.get("reason")=="unexpected_close" for e in phases)
        closure_expected=bool(last_closure and not last_closure.get("reason")
                              and not closure_unexpected)
        strict_done_receipt=False
        if state=="done" and valid:
            # Reuse the bridge's canonical receipt and URL proof, never a filename alone.
            try:
                from bridge import receipt_closure_candidate
                strict_done_receipt=receipt_closure_candidate(jid)[0]
            except (OSError,ValueError,TypeError,AttributeError):
                pass
        recovery_eligible=False
        recovery_reason="not_active"
        if state=="active":
            if (t.ACTIVE/(jid+".json")).is_symlink():
                recovery_reason="untrusted_state_path"
            else:
                try:
                    from bridge import recovery_candidate
                    recovery_eligible,recovery_reason=recovery_candidate(jid)
                except (OSError,ValueError,TypeError,AttributeError):
                    recovery_reason="recovery_check_unavailable"
        decision=reconciliation_decision(
            state=state,valid_ack=valid,has_report=has_report,
            expected_close=closure_expected,unexpected_close=closure_unexpected,
            events=phases,strict_done_receipt=strict_done_receipt)
        if decision=="await_browser_identity" and not recovery_eligible:
            decision="manual_recovery_ineligible"
        rows.append({
            "role":role,"job_id":jid,"state":state,
            "events":phases,"last_event":phases[-1] if phases else None,
            "tab_created":"tab_created" in first,
            "send_clicked":"send_clicked" in first,
            "message_saved":"message_saved" in first,
            "report_exists":has_report,"report_sha_matches_ack":valid,
            "saved_after_latest_send":saved_after_latest_send(phases),
            "reconciliation_decision":decision,
            "server_recovery_eligible":recovery_eligible,
            "server_recovery_reason":recovery_reason,
            "server_done":state=="done",
            "tab_closed":"tab_closed" in first,
            "tab_closed_expected":closure_expected,
            "tab_closed_unexpected":closure_unexpected,
            "acknowledged_closure":decision=="local_pipeline_confirmed",
            "send_to_report_seconds":elapsed(first.get("send_clicked"),created(report)),
            "last_event_age_seconds":elapsed(parse(phases[-1]["at"]) if phases else None,now),
            "errors":error[-5:],
            "produced_files":outputs,
            "model_compute_seconds":None})
    scan=safe_json(t.DIAG/"recovery-scan.json") or {}
    ext=safe_json(t.DIAG/"extension.json") or {}
    return {"ok":True,"asof_utc":now.isoformat(timespec="seconds"),
            "extension_version":ext.get("version"),"scan":{k:scan.get(k) for k in (
                "at","tabs_total","chat_tabs","stored_owned","identified",
                "recovered","stored_conflict","stored_invalid")},
            "counts":{
                "pending":sum(x["state"]=="queue" for x in rows),
                "active":sum(x["state"]=="active" for x in rows),
                "done":sum(x["state"]=="done" for x in rows),
                "state_conflicts":sum(x["state"]=="conflict" for x in rows),
                "tab_created":sum(x["tab_created"] for x in rows),
                "message_sent":sum(x["send_clicked"] for x in rows),
                "message_saved":sum(x["message_saved"] for x in rows),
                "reports":sum(x["report_exists"] for x in rows),
                "acks_valid":sum(x["report_sha_matches_ack"] for x in rows),
                "closed":sum(x["tab_closed"] for x in rows),
                "closed_expected":sum(x["tab_closed_expected"] for x in rows),
                "closed_unexpected":sum(x["tab_closed_unexpected"] for x in rows),
                "acknowledged_closure":sum(x["acknowledged_closure"] for x in rows),
                "manual_reconciliation_required":sum(
                    x["reconciliation_decision"].startswith("manual_") for x in rows),
                "await_browser_identity":sum(
                    x["reconciliation_decision"]=="await_browser_identity" for x in rows),
                "exceptions":sum(bool(x["errors"]) for x in rows)
            },
            "legacy":{
                "held":t.HOLD.is_file(),
                "active":len(list(t.ACTIVE.glob("*.json")))-sum(
                    (t.ACTIVE/(jid+".json")).exists() for jid in t.IDS),
                "queued":len(list(t.LEGACY_QUEUE.glob("*.json")))
            },
            "agents":rows}
if __name__=="__main__":
    print(json.dumps(snapshot(),ensure_ascii=False,indent=2))
