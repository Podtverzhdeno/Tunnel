"""Read-only evidence-oriented Chain2 blocker dossier.

Never queues, retries, writes markers/receipts or sends browser messages.
Produces only job IDs, state labels, bounded evidence and action categories;
never prompts, account details, report contents or session tokens.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import chain_campaign as c
from advisory_completion import _uuid, _load_job, _read_report, Pending

EVENT_STAGES={
    "tab_opening","tab_created","assigned","editor_found","prompt_filled",
    "send_clicked","message_saved","handoff_failed","done","submitted",
    "unknown","watchdog_timeout","receipt_accepted","receipt_failed",
    "tab_closed","tab_close_failed","tab_missing","tab_close_skipped",
    "tab_preserved","auth_context_changed","runtime_error","report_pending",
    "report_acked","report_watchdog","report_disputed","tab_recovered",
    "tab_restored","tab_identity_unverified","tab_binding_disputed",
    "send_ui_diagnostic"
}
PRE_SEND_REASONS={"send_button_missing","editor_not_found","editor_fill_failed"}
AMBIGUOUS_REASONS={"account_context_changed","unexpected_close","unknown",
                   "ambiguous_probe_outcome","watchdog_timeout"}

def bounded_json(path:Path,limit:int):
    """Read bounded evidence; reject missing, linked or concurrently growing files."""
    try:
        if (type(limit) is not int or limit<1 or path.is_symlink()
                or getattr(path,"is_junction",lambda:False)()
                or not path.is_file() or not 0<path.stat().st_size<=limit):
            return None
        with path.open("rb") as source:
            raw=source.read(limit+1)
        if not 0<len(raw)<=limit:
            return None
        return json.loads(raw)
    except (OSError,ValueError,UnicodeError):
        return None

def events_for(job_id):
    record=bounded_json(c.STATE/"diagnostics"/(job_id+".json"),65536)
    if type(record) is not list:return [],"missing_or_invalid"
    events=[]
    # The telemetry file itself is capped at 64 KiB. Inspect its entire
    # validated history: a prior message_saved/auth dispute must not disappear
    # merely because later watchdog/close events filled the last 60 entries.
    for row in record:
        if type(row) is not dict or row.get("stage") not in EVENT_STAGES:
            continue
        reason=row.get("reason")
        if type(reason) is not str or len(reason)>48:
            reason=None
        at=row.get("at")
        if type(at) is not str or len(at)>40:at=None
        events.append({"stage":row["stage"],"reason":reason,"at":at})
    return events,"available"

def receipt_status(job_id,report_path):
    ack_path=c.ACKS/(job_id+".json")
    ack=bounded_json(ack_path,65536)
    ack_exists=ack_path.is_file() and not ack_path.is_symlink()
    try:
        if not report_path.is_file() or report_path.is_symlink():
            return {"report_exists":False,"report_sha256":None,
                    "ack_exists":ack_exists,"ack_matches":False}
        if not 0<report_path.stat().st_size<=150000:
            return {"report_exists":True,"report_sha256":None,
                    "ack_exists":ack_exists,"ack_matches":False}
        with report_path.open("rb") as source:
            body=source.read(150001)
        if not 0<len(body)<=150000:
            return {"report_exists":True,"report_sha256":None,
                    "ack_exists":ack_exists,"ack_matches":False}
    except OSError:
        return {"report_exists":False,"report_sha256":None,
                "ack_exists":ack_exists,"ack_matches":False}
    digest=hashlib.sha256(body).hexdigest()
    valid=(type(ack) is dict and ack.get("job_id")==job_id
        and ack.get("status")=="REPORT_ACKED"
        and ack.get("artifact_sha256")==digest
        and type(ack.get("ack_id")) is str and bool(ack["ack_id"]))
    return {"report_exists":True,"report_sha256":digest,
            "ack_exists":ack_path.is_file(),"ack_matches":valid}

def report_preflight(job_id,active_root,report_root):
    """Pure read-only integrity check; does not create or approve an ACK."""
    try:
        jid=_uuid(job_id)
        _load_job(active_root,jid)
        sha, size=_read_report(report_root,jid)
        return {"report_integrity_ready":True,"report_sha256":sha,
                "report_bytes":size,"would_create_ack":False,
                "host_identity_verified":False}
    except Pending as e:
        return {"report_integrity_ready":False,"pending_reason":e.code,
                "would_create_ack":False,"host_identity_verified":False}
    except (OSError,ValueError,TypeError):
        return {"report_integrity_ready":False,
                "pending_reason":"read_only_validation_error",
                "would_create_ack":False,"host_identity_verified":False}

def request_preflight(ticket,role,job_id,digest,tickets,seed_count):
    """Preview Chain2 request checks, without invoking request or trusting role labels.

    A positive result means ONLY source/metadata preconditions are met. It
    never grants actor identity, owner approval or permission to create a job.
    """
    result={"structural_preconditions_met":False,
            "trusted_actor_verified":False,"request_created":False}
    try:
        state,job=c.locate(job_id)
        if state!="active" or type(job) is not dict:
            return {**result,"reason":"not_active"}
        if not c.armed(digest,len(tickets)):
            return {**result,"reason":"campaign_not_armed"}
        expected={"campaign_id":"chain2","ticket_id":ticket,
                  "stage":role,"manifest_sha256":digest}
        if job_id!=c.jid(ticket,role) or job.get("role_metadata")!=expected:
            return {**result,"reason":"role_metadata_not_bound"}
        if c.marker(job_id) is not None:
            return {**result,"reason":"immutable_request_exists"}
        c.read_handoff(job_id,ticket,role)
        return {**result,"structural_preconditions_met":True,
                "reason":"original_actor_request_required"}
    except (OSError,ValueError,KeyError,TypeError,c.ChainError) as e:
        # Do not surface arbitrary exception details from untrusted files.
        return {**result,"reason":"handoff_or_state_validation_failed"}

def role_path_status(root:Path,relative:Path):
    """Classify a role-local file without following symlink/junction segments.

    A role root or child may be replaced between inspection and use; this is
    advisory only, never an artifact authorization or a reason to issue ACK.
    """
    try:
        if (root.is_symlink() or getattr(root,"is_junction",lambda:False)()):
            return "unsafe"
        approved=root.resolve(strict=True)
        node=root
        for part in relative.parts:
            node=node/part
            if node.is_symlink() or getattr(node,"is_junction",lambda:False)():
                return "unsafe"
        if not node.is_file():
            return "missing"
        return "valid" if node.resolve(strict=True).is_relative_to(approved) else "unsafe"
    except (OSError,RuntimeError,ValueError):
        return "unsafe"


def diagnose_handoff_paths(ticket,role):
    """Read-only hints. Never normalize an invalid handoff into an ACK.

    changed_files must be relative to the role workspace. An unexpected
    project-root prefix is a repair candidate, not an automatic acceptance.
    No content outside the allowlisted role workspace is read.
    """
    handoff=c.handoff_path(ticket,role)
    data=bounded_json(handoff,20000)
    if type(data) is not dict or type(data.get("changed_files")) is not list:
        return {"status":"handoff_unreadable_or_schema_invalid"}
    root=c.workspace(ticket,role)
    expected_prefix=("chat-launch-smoke-desktop-test","experiments",
                     "role-workstreams","chain2",ticket,role)
    valid=0;prefixed=0;missing=0;invalid=0
    candidates=[]
    for name in data["changed_files"][:30]:
        if type(name) is not str or len(name)>350:
            invalid+=1;continue
        rel=Path(name)
        if (not name or rel.is_absolute() or ".." in rel.parts
            or ":" in name or "\\" in name or not rel.parts):
            invalid+=1;continue
        direct_status=role_path_status(root,rel)
        if direct_status=="valid":
            valid+=1;continue
        if direct_status=="unsafe":
            invalid+=1;continue
        pieces=rel.parts
        if len(pieces)>len(expected_prefix) and pieces[:len(expected_prefix)]==expected_prefix:
            candidate=Path(*pieces[len(expected_prefix):])
            candidate_status=role_path_status(root,candidate)
            if candidate_status=="valid":
                prefixed+=1
                candidates.append(str(candidate).replace("\\","/"))
                continue
            if candidate_status=="unsafe":
                invalid+=1;continue
        missing+=1
    return {"status":"workspace_prefix_repair_candidate" if prefixed and
            not missing and not invalid else "requires_manual_artifact_inspection",
            "workspace_relative_valid":valid,"prefixed_but_physically_present":prefixed,
            "missing":missing,"unsafe":invalid,
            "suggested_role_relative_names":candidates,
            "automatic_rewrite_permitted":False}

def classify(record):
    """Conservative, priority-ordered classification; never calls this DONE."""
    events=record["events"]
    reasons={x["reason"] for x in events if x.get("reason")}
    stages={x["stage"] for x in events}
    sent="message_saved" in stages
    clicked="send_clicked" in stages
    # A verified tab restoration supersedes an earlier close. Mere
    # tab_opening/failed recovery must not do so.
    closed=False
    for event in events:
        if event["stage"]=="tab_closed":
            closed=True
        elif event["stage"] in ("tab_restored","tab_recovered","tab_created"):
            closed=False
    if "account_context_changed" in reasons or "auth_context_changed" in stages:
        return "ACCOUNT_CONTEXT_DISPUTED"
    if not sent and clicked:
        return "CLICKED_BUT_UNCONFIRMED"
    if not sent and PRE_SEND_REASONS.intersection(reasons):
        return "PRE_SEND_UNCONFIRMED"
    if closed and not record["receipt"]["ack_matches"]:
        return "CLOSED_WITHOUT_VERIFIED_ACK"
    if sent and record["receipt"]["report_exists"] and not record["receipt"]["ack_matches"]:
        if not record["handoff_valid"]:
            return "REPORT_PRESENT_HANDOFF_BLOCKED"
        if not record["request_valid"]:
            return "REPORT_PRESENT_REQUEST_MISSING"
        return "REPORT_PRESENT_ACK_PENDING"
    if record["handoff_exists"] and not record["handoff_valid"]:
        return "HANDOFF_INVALID"
    if record["handoff_valid"] and not record["request_valid"]:
        return "VALID_HANDOFF_REQUEST_MISSING"
    if sent and not record["receipt"]["report_exists"]:
        return "SENT_BUT_REPORT_MISSING"
    if not sent:
        return "DELIVERY_UNVERIFIED"
    if record["receipt"]["ack_matches"]:
        return "ACK_EXISTS_RESULT_NOT_DONE"
    return "ACTIVE_UNRESOLVED"

def recommended_next(status):
    recommendations={
        "ACCOUNT_CONTEXT_DISPUTED":"Operator verify real browser/account identity; do not replay or clear tabs.",
        "CLICKED_BUT_UNCONFIRMED":"Independent browser/message reconciliation required before any retry.",
        "PRE_SEND_UNCONFIRMED":"Inspect preserved browser/event evidence and operator-confirm absence of send; no automatic replay.",
        "CLOSED_WITHOUT_VERIFIED_ACK":"Keep ACTIVE and original report; reconcile tab ownership and report ACK safely.",
        "REPORT_PRESENT_HANDOFF_BLOCKED":"Verify isolated handoff artifacts and hashes; do not fabricate new evidence.",
        "REPORT_PRESENT_REQUEST_MISSING":"Check original agent's verified request receipt; never request as the agent.",
        "REPORT_PRESENT_ACK_PENDING":"Read-only validate report schema, ACK conditions and existing immutable request.",
        "HANDOFF_INVALID":"Verify handoff paths and integrity; only original authorized actor can repair.",
        "VALID_HANDOFF_REQUEST_MISSING":"Check original request result, SHA and exact job ID; do not self-authorize.",
        "SENT_BUT_REPORT_MISSING":"Check agent session and receipt; continue only through authorized session.",
        "DELIVERY_UNVERIFIED":"No evidence of message_saved; preserve job and investigate.",
        "ACK_EXISTS_RESULT_NOT_DONE":"Verify exact ACK against role metadata before scoped result reconciliation.",
        "ACTIVE_UNRESOLVED":"Read-only collect more provider/host evidence; no mutation."
    }
    return recommendations[status]

def analyze():
    cfg,tickets,digest=c.load()
    rows=[]
    for ticket in tickets:
        for role in c.ROLES:
            jid=c.jid(ticket["id"],role)
            state,job=c.locate(jid)
            if state!="active":continue
            events,event_status=events_for(jid)
            handoff_exists=c.handoff_path(ticket["id"],role).is_file()
            handoff_valid=False
            handoff_failure=None
            if handoff_exists:
                try:
                    c.read_handoff(jid,ticket["id"],role)
                    handoff_valid=True
                except (OSError,ValueError,TypeError,c.ChainError) as e:
                    handoff_failure=str(e)[:80]
            receipt=receipt_status(jid,c.REPORTS/("completion-"+jid+".md"))
            row={"ticket":ticket["id"],"role":role,"job_id":jid,
                 "state":"active","delivery_message_saved":any(e["stage"]=="message_saved" for e in events),
                 "delivery_send_clicked":any(e["stage"]=="send_clicked" for e in events),
                 "last_event":events[-1] if events else None,
                 "telemetry_status":event_status,
                 "events":events,"handoff_exists":handoff_exists,
                 "handoff_valid":handoff_valid,"handoff_failure":handoff_failure,
                 "request_recorded":c.marker(jid) is not None,
                 "request_valid":c.request_valid(ticket["id"],role,digest,tickets,cfg["seed_count"]),
                 "receipt":receipt,
                 "report_preflight":report_preflight(jid,c.ACTIVE,c.REPORTS),
                 "request_preflight":request_preflight(ticket["id"],role,jid,digest,tickets,cfg["seed_count"])}
            if handoff_exists and not handoff_valid:
                row["handoff_path_diagnostics"]=diagnose_handoff_paths(ticket["id"],role)
            row["classification"]=classify(row)
            row["next_step"]=recommended_next(row["classification"])
            row.pop("events")
            rows.append(row)
    kinds=Counter(row["classification"] for row in rows)
    return {"ok":True,"mode":"READ_ONLY","campaign":"chain2",
            "active_count":len(rows),"summary":dict(sorted(kinds.items())),
            "blockers":rows,"no_queue_mutations":True,
            "no_retries_or_result_forgery":True,
            "note":"ACTIVE is durable server state, not evidence that a ChatGPT model is currently working."}

if __name__=="__main__":
    print(json.dumps(analyze(),ensure_ascii=False))
