"""Read-only Chain2 parent -> successor lineage monitor.

Conservatively differentiate an immutable handoff request, scheduler admission,
browser send confirmation and report completion. Does not claim, enqueue,
resume, repair, or close anything. Never exposes prompts or account data.
"""
from __future__ import annotations
import json
from pathlib import Path
import chain_campaign as c

SIGNIFICANT_STAGES=("message_saved","report_acked","receipt_accepted","tab_closed",
                    "unknown","send_ui_diagnostic","report_disputed",
                     "auth_context_changed","tab_binding_disputed","tab_identity_unverified")

def events_for(job_id):
    path=c.STATE/"diagnostics"/(job_id+".json")
    if not path.is_file() or path.is_symlink() or path.stat().st_size>65536:
        return []
    try:
        events=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(events,list):return []
        return [x for x in events if isinstance(x,dict) and
                x.get("stage") in SIGNIFICANT_STAGES]
    except (ValueError,OSError,UnicodeError):
        return []

def inspect():
    cfg,tickets,digest=c.load()
    out=[]
    problems=[]
    total=2*len(tickets)
    counts={"done":0,"active":0,"queue":0,"not_started":0,
            "done_valid_requests":0,"done_ack_verified":0,"successors_admitted":0,
            "successors_message_saved":0,"successors_waiting_admission":0,
            "terminal_done":0,"active_valid_handoff_waiting_request":0,
            "active_invalid_handoff":0}
    for ticket in tickets:
        for role in c.ROLES:
            ticket_id=ticket["id"]
            job_id=c.jid(ticket_id,role)
            state,_=c.locate(job_id)
            if state is None:
                counts["not_started"]+=1
                continue
            counts[state]+=1
            child_ticket,child_role=c.successor(ticket_id,role,tickets,cfg["seed_count"])
            child_id=c.jid(child_ticket,child_role) if child_ticket else None
            child_state,child_data=c.locate(child_id) if child_id else (None,None)
            handoff_exists=c.handoff_path(ticket_id,role).is_file()
            request=c.marker(job_id)
            request_valid=c.request_valid(ticket_id,role,digest,tickets,cfg["seed_count"])
            report_accepted=c.accepted(ticket_id,role,digest,tickets,cfg["seed_count"]) if state=="done" else False
            child_events=events_for(child_id) if child_id else []
            saved=any(x.get("stage")=="message_saved" for x in child_events)
            alert_child=[{"stage":x["stage"],"reason":x.get("reason")}
                         for x in child_events if x.get("stage") in
                         ("unknown","report_disputed","send_ui_diagnostic",
                          "auth_context_changed","tab_binding_disputed",
                          "tab_identity_unverified") or
                         x.get("reason") in ("unexpected_close","account_context_changed")]
            dispute=any(x.get("stage") in (
                "auth_context_changed","tab_binding_disputed","tab_identity_unverified")
                or x.get("reason")=="account_context_changed" for x in child_events)
            alert_child=alert_child[-15:]
            row={"ticket":ticket_id,"role":role,"job_id":job_id,"state":state,
                 "handoff_exists":handoff_exists,"request_recorded":request is not None,
                 "request_valid":request_valid,"report_ack_verified":report_accepted,
                 "successor_job_id":child_id,"successor_ticket":child_ticket,
                 "successor_role":child_role,"successor_state":child_state,
                 "successor_message_saved":saved,"successor_incidents":alert_child}
            if state=="done":
                if request_valid:counts["done_valid_requests"]+=1
                else:problems.append({"severity":"critical","job_id":job_id,
                                      "reason":"done_missing_or_invalid_handoff_request"})
                if report_accepted:counts["done_ack_verified"]+=1
                else:problems.append({"severity":"critical","job_id":job_id,
                                      "reason":"done_without_verified_report_ack"})
                if not child_id:
                    counts["terminal_done"]+=1
                elif child_state in ("queue","active","done"):
                    counts["successors_admitted"]+=1
                    if saved or child_state=="done":counts["successors_message_saved"]+=1
                    if child_state=="active" and dispute:
                        problems.append({"severity":"warning","job_id":child_id,
                                         "parent_job_id":job_id,
                                         "reason":"child_account_context_disputed"})
                    if child_state=="active" and not saved and alert_child:
                        problems.append({"severity":"warning","job_id":child_id,
                                         "parent_job_id":job_id,"reason":"child_claimed_but_send_unconfirmed",
                                         "incidents":alert_child})
                else:
                    counts["successors_waiting_admission"]+=1
                    problems.append({"severity":"warning","job_id":job_id,
                                     "successor_job_id":child_id,"reason":"accepted_parent_without_admitted_successor"})
            elif state=="active" and handoff_exists and not request_valid:
                try:
                    c.read_handoff(job_id,ticket_id,role)
                    row["handoff_valid"]=True
                    counts["active_valid_handoff_waiting_request"]+=1
                    problems.append({"severity":"watch","job_id":job_id,
                                     "reason":"active_handoff_valid_but_successor_not_requested"})
                except (ValueError,OSError,TypeError,c.ChainError) as e:
                    counts["active_invalid_handoff"]+=1
                    row["handoff_valid"]=False
                    row["handoff_error"]=str(e)
                    problems.append({"severity":"warning","job_id":job_id,
                                     "reason":"active_handoff_invalid","detail":str(e)})
            out.append(row)
    return {"ok":True,"mode":"READ_ONLY","campaign":"chain2",
            "total_stages":total,"summary":counts,
            "chain_armed":c.armed(digest,len(tickets)),"admission_hold":c.HOLD.exists(),
            "problems":problems,"lineage":out,
            "no_mutations_performed":True}

if __name__=="__main__":
    print(json.dumps(inspect(),ensure_ascii=False))
