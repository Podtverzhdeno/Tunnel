"""Owner-armed, unbounded *sequence* of verified developer/reviewer successors.

No fixed generation limit. Never unbounded concurrency or automatic replays.
Selectable sandbox/trusted policy. Trusted choice is not host authorization;
without an independent P085 host attestation it always fails closed.
Inactive until the existing finite Chain2 campaign is fully accepted. A lane
can stop when no genuine, approved maintenance work remains. All identities
are deterministic; a child is admitted only after its parent's SHA-bound
handoff request, report ACK and server DONE. No merge/deploy grants.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chain_campaign as previous
import role_campaign as legacy
from enqueue_completion import enqueue
from chain3_work_quality import QualityError, validate_decision, validate_novel_issue

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
STATE=ROOT/"bridge-state"
QUEUE,ACTIVE,DONE=(STATE/x for x in ("queue","active","done"))
ACKS,REPORTS=STATE/"completion-acks",PROJECT/"agent-reports"
BRIEFS=PROJECT/"agent-briefs"
CONFIG=ROOT/"role-campaigns"/"chain3.json"
HOLD=STATE/"admission-hold.json"
ARM=STATE/"continuous-armed.json"
REQUESTS=STATE/"continuous-handoff-requests"
RATE=STATE/"continuous-claim-rate.json"
MODE=STATE/"chain3-mode.json"
MODES=("sandbox","trusted")
ROLES=("developer","reviewer")
ROLE_PATTERN=re.compile(r"l([0-9]{2})-g([0-9]+)\Z")
SHA=re.compile(r"[0-9a-f]{64}\Z")

class ContinuationError(RuntimeError):pass

def _read_json(path,limit=65536):
    if not path.is_file() or path.is_symlink() or not 0<path.stat().st_size<=limit:
        raise ContinuationError("missing_or_unsafe_file")
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError,OSError,UnicodeError) as e:
        raise ContinuationError("invalid_json") from e

def selected_mode():
    """Local preference only; never a trusted owner or host attestation."""
    if not MODE.exists() and not MODE.is_symlink():
        return "sandbox"
    data=_read_json(MODE,4096)
    if (type(data) is not dict or set(data)!={"schema_version","campaign_id","mode"}
        or data.get("schema_version")!=1 or data.get("campaign_id")!="chain3"
        or type(data.get("mode")) is not str or data["mode"] not in MODES):
        raise ContinuationError("invalid_mode_selection")
    return data["mode"]

def trusted_host_attested():
    """A protected host attestation channel is not yet installed.
    Agent-writable diagnostics, CLI flags and JSON cannot grant OS authority.
    """
    return False

def mode_can_run():
    mode=selected_mode()
    return mode=="sandbox" or (mode=="trusted" and trusted_host_attested())

def mode_status():
    mode=selected_mode()
    trusted=(mode=="trusted" and trusted_host_attested())
    return {"selected_mode":mode,"effective_mode":(
        "trusted" if trusted else "sandbox" if mode=="sandbox" else "blocked"),
        "trusted_host_verified":trusted,
        "trusted_mode_requires":"OS_PROTECTED_P085_BROKER_AND_INDEPENDENT_ATTESTATION",
        "mode_selection_is_authorization":False}

def select_mode(mode,confirm_owner=False):
    """Select policy before the first Chain3 arm/job. Never grant permissions."""
    if not confirm_owner:raise ContinuationError("explicit_mode_selection_required")
    if type(mode) is not str or mode not in MODES:
        raise ContinuationError("invalid_mode")
    current=selected_mode()
    if (ARM.exists() or ARM.is_symlink()
        or (REQUESTS.exists() and any(REQUESTS.glob("*.json")))):
        raise ContinuationError("mode_selection_locked_existing_chain3_state")
    for folder in (QUEUE,ACTIVE,DONE):
        for path in folder.glob("*.json"):
            try:
                record=_read_json(path)
            except (OSError,ContinuationError):
                raise ContinuationError("mode_selection_state_unverifiable")
            if (type(record) is dict and type(record.get("role_metadata")) is dict
                and record["role_metadata"].get("campaign_id")=="chain3"):
                raise ContinuationError("mode_selection_locked_existing_chain3_state")
    if current==mode:
        return {"ok":True,"already_selected":True,**mode_status()}
    MODE.parent.mkdir(parents=True,exist_ok=True)
    payload={"schema_version":1,"campaign_id":"chain3","mode":mode}
    temp=MODE.with_name("chain3-mode-"+uuid.uuid4().hex+".writing")
    try:
        with temp.open("x",encoding="utf-8") as f:
            json.dump(payload,f);f.flush();os.fsync(f.fileno())
        os.replace(temp,MODE)
    finally:
        temp.unlink(missing_ok=True)
    return {"ok":True,"changed":True,**mode_status()}

def load():
    config=_read_json(CONFIG,4000)
    fields={"schema_version","campaign_id","continuation_of","roles","seed_count",
            "max_active","max_queued","min_claim_interval_seconds",
            "max_claims_per_utc_day","requires_owner_arm","scope","admission","policy"}
    if type(config) is not dict or set(config)!=fields:
        raise ContinuationError("invalid_config_keys")
    if (config["schema_version"]!=1 or config["campaign_id"]!="chain3"
        or config["continuation_of"]!="chain2"
        or config["roles"]!=list(ROLES)
        or config["requires_owner_arm"] is not True
        or config["scope"]!="approved_open_spec_sdd_qa_only"
        or config["admission"]!="parent_report_ack_and_handoff"
        or config["policy"]!="meaningful_work_required_no_proposals_autoapproved"
        or type(config["min_claim_interval_seconds"]) is not int
        or not 60<=config["min_claim_interval_seconds"]<=86400
        or type(config["max_claims_per_utc_day"]) is not int
        or not 1<=config["max_claims_per_utc_day"]<=60
        or type(config["max_active"]) is not int
        or not 1<=config["max_active"]<=10
        or type(config["max_queued"]) is not int
        or not 1<=config["max_queued"]<=4):
        raise ContinuationError("unsafe_config")
    old_cfg,tickets,old_digest=previous.load()
    if config["seed_count"]!=old_cfg["seed_count"]:
        raise ContinuationError("seed_count_mismatch")
    raw=CONFIG.read_bytes()
    digest=hashlib.sha256(raw+old_digest.encode("ascii")).hexdigest()
    return config,tickets,old_digest,digest

def label(lane,generation):
    if type(lane) is not int or type(generation) is not int or lane<0 or generation<0:
        raise ContinuationError("invalid_generation")
    return f"l{lane:02d}-g{generation:08d}"

def parse_label(value,seed_count):
    m=ROLE_PATTERN.fullmatch(value) if type(value) is str else None
    if not m:raise ContinuationError("invalid_stage_label")
    lane,generation=int(m.group(1)),int(m.group(2))
    if lane>=seed_count or value!=label(lane,generation):
        raise ContinuationError("invalid_lane_or_generation")
    return lane,generation

def jid(lane,generation,role):
    if role not in ROLES:raise ContinuationError("invalid_role")
    return str(uuid.uuid5(legacy.NAMESPACE,
        f"chain3/{label(lane,generation)}/{role}"))

def role_name(lane,generation,role):
    suffix="dev" if role=="developer" else "rev"
    result=f"rw-c3-{label(lane,generation)}-{suffix}"
    if not legacy.ROLE.fullmatch(result):
        raise ContinuationError("role_name_too_long")
    return result

def workspace(lane,generation,role):
    return PROJECT/"experiments"/"role-workstreams"/"chain3"/label(lane,generation)/role

def handoff_path(lane,generation,role):
    return workspace(lane,generation,role)/"handoff.json"

def last_chain2_ticket(lane,tickets,seed_count):
    positions=list(range(lane,len(tickets),seed_count))
    return tickets[positions[-1]]["id"]

def chain2_finished():
    cfg,tickets,digest=previous.load()
    # Check ALL finite stages rather than only terminal lanes, so missing
    # or disputed old reports cannot be hidden behind a terminal handoff.
    for ticket in tickets:
        for role in previous.ROLES:
            if not previous.accepted(ticket["id"],role,digest,tickets,cfg["seed_count"]):
                return False
    return True

def armed(digest):
    try:
        if not mode_can_run():return False
        x=_read_json(ARM,4096)
        return (type(x) is dict and x.get("enabled") is True
                and x.get("campaign_id")=="chain3"
                and x.get("manifest_sha256")==digest
                and x.get("owner_authorized") is True)
    except (OSError,ContinuationError):return False

def arm(confirm_owner=False):
    if not confirm_owner:raise ContinuationError("explicit_owner_arm_required")
    if not mode_can_run():raise ContinuationError("trusted_host_attestation_required")
    cfg,tickets,old_digest,digest=load()
    if ARM.exists():
        if armed(digest):return {"ok":True,"already_armed":True}
        raise ContinuationError("owner_arm_conflict")
    payload={"enabled":True,"campaign_id":"chain3","manifest_sha256":digest,
             "owner_authorized":True,
             "armed_at":datetime.now(timezone.utc).isoformat(),
             "source_chain2_digest":old_digest}
    ARM.parent.mkdir(parents=True,exist_ok=True)
    with ARM.open("x",encoding="utf-8") as f:
        json.dump(payload,f)
        f.flush();os.fsync(f.fileno())
    return {"ok":True,"armed":True,"mode":"DORMANT_UNTIL_CHAIN2_ACCEPTED",
            "generation_limit":None,"concurrency_limit":cfg["max_active"],
            "daily_claim_limit":cfg["max_claims_per_utc_day"]}

def locate(lane,generation,role):
    return previous.locate(jid(lane,generation,role))

def predecessor(lane,generation,role,tickets,seed_count):
    if role=="reviewer":
        return ("chain3",lane,generation,"developer")
    if generation>0:
        return ("chain3",lane,generation-1,"reviewer")
    return ("chain2",last_chain2_ticket(lane,tickets,seed_count),"reviewer",None)

def successor(lane,generation,role):
    return (lane,generation,"reviewer") if role=="developer" else (lane,generation+1,"developer")

def upstream_path(lane,generation,role,tickets,seed_count):
    p=predecessor(lane,generation,role,tickets,seed_count)
    if p[0]=="chain2":
        return (f"chat-launch-smoke-desktop-test/experiments/role-workstreams/"
                f"chain2/{p[1]}/{p[2]}/handoff.json")
    return (f"chat-launch-smoke-desktop-test/experiments/role-workstreams/"
            f"chain3/{label(p[1],p[2])}/{p[3]}/handoff.json")

def handoff_sha(lane,generation,role,tickets,seed_count):
    # Validate the role-workspace ancestry BEFORE reading handoff.json.
    # The role directory must not be a link, junction, or escape via a parent.
    role_dir=workspace(lane,generation,role)
    allowed=(PROJECT/"experiments"/"role-workstreams"/"chain3").resolve()
    try:
        node=PROJECT
        for part in role_dir.relative_to(PROJECT).parts:
            node=node/part
            if node.is_symlink() or getattr(node,"is_junction",lambda:False)():
                raise ContinuationError("workspace_escape")
        base=role_dir.resolve()
        if not base.is_relative_to(allowed):
            raise ContinuationError("workspace_escape")
    except ContinuationError:
        raise
    except (OSError,ValueError,RuntimeError) as exc:
        raise ContinuationError("workspace_escape") from exc
    path=handoff_path(lane,generation,role)
    data=_read_json(path,20000)
    fields={"version","parent_job_id","upstream_handoff","summary","changed_files",
            "tests","issues","feature_proposals","next_focus","no_actionable_work"}
    optional={"files_sha256","production_authorization_ready","owner_approval_obtained","next_work"}
    if (type(data) is not dict or not fields.issubset(data.keys())
        or not set(data).issubset(fields|optional)
        or data["version"]!=1 or
        data["parent_job_id"]!=jid(lane,generation,role) or
        data["upstream_handoff"]!=upstream_path(lane,generation,role,tickets,seed_count)
        or type(data["no_actionable_work"]) is not bool
        or (role=="developer" and data["no_actionable_work"] is True)
        or data.get("production_authorization_ready",False) is not False
        or data.get("owner_approval_obtained",False) is not False):
        raise ContinuationError("handoff_schema_or_authority_invalid")
    for f in ("summary","next_focus"):
        if type(data[f]) is not str or not 15<=len(data[f].strip())<=1800:
            raise ContinuationError("handoff_text_invalid")
    for f,max_count in (("changed_files",24),("tests",32),("issues",24),
                        ("feature_proposals",16)):
        if type(data[f]) is not list or len(data[f])>max_count:
            raise ContinuationError("handoff_list_invalid")
        if any(type(x) is not str or not 3<=len(x)<=400 for x in data[f]):
            raise ContinuationError("handoff_list_item_invalid")
    if not data["changed_files"] or not data["tests"]:
        raise ContinuationError("missing_work_evidence")
    # The role root was validated before handoff.json was opened.
    if len(set(data["changed_files"]))!=len(data["changed_files"]):
        raise ContinuationError("duplicate_artifacts")
    files_sha=data.get("files_sha256",{})
    if type(files_sha) is not dict or len(files_sha)>24:
        raise ContinuationError("artifact_hash_map_invalid")
    for name,expected in files_sha.items():
        if name not in data["changed_files"] or type(expected) is not str or not SHA.fullmatch(expected):
            raise ContinuationError("artifact_hash_reference_invalid")
    # Single bounded streaming pass: no unbounded read_bytes() on optional
    # hashes, and a file that grows during hashing cannot pass verification.
    artifact_hashes={}
    for name in data["changed_files"]:
        rel=Path(name)
        if rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name:
            raise ContinuationError("artifact_traversal")
        candidate=role_dir
        for part in rel.parts:
            candidate=candidate/part
            if candidate.is_symlink() or getattr(candidate,"is_junction",lambda:False)():
                raise ContinuationError("artifact_link_invalid")
        try:
            if not candidate.is_file() or not candidate.resolve(strict=True).is_relative_to(base):
                raise ContinuationError("artifact_missing")
            before=candidate.stat().st_size
            if before>16*1024*1024:
                raise ContinuationError("artifact_too_large_for_handoff")
            digest_file=hashlib.sha256()
            total=0
            with candidate.open("rb") as artifact:
                for chunk in iter(lambda: artifact.read(65536),b""):
                    total+=len(chunk)
                    if total>16*1024*1024:
                        raise ContinuationError("artifact_too_large_for_handoff")
                    digest_file.update(chunk)
            if total!=before or candidate.stat().st_size!=before:
                raise ContinuationError("artifact_changed_during_hash")
        except ContinuationError:
            raise
        except (OSError,RuntimeError,ValueError) as exc:
            raise ContinuationError("artifact_unreadable") from exc
        hashed=digest_file.hexdigest()
        if name in files_sha and hashed!=files_sha[name]:
            raise ContinuationError("artifact_sha_changed")
        artifact_hashes[name]=digest_file.digest()
    if role=="reviewer":
        source=last_chain2_ticket(lane,tickets,seed_count)
        approved=next(t for t in tickets if t["id"]==source)
        decision=validate_decision(
            decision=data.get("next_work"),
            no_actionable_work=data["no_actionable_work"],
            approved_ticket=approved,workspace=workspace(lane,generation,role),
            changed_files=data["changed_files"])
        historical=[]
        for old_generation in range(generation):
            old=_read_json(handoff_path(lane,old_generation,"reviewer"),20000)
            if old.get("no_actionable_work") is False:
                historical.append(old.get("next_work"))
        validate_novel_issue(decision,historical)
    elif data.get("next_work") is not None:
        raise ContinuationError("developer_cannot_self_approve_next_work")
    # The immutable request must bind every listed artifact, not just the
    # handoff JSON (files_sha256 is optional for older role handoff writers).
    # This is structural integrity, not trusted evidence of test execution.
    digest=hashlib.sha256()
    digest.update(b"chain3-handoff-bundle-v1\\x00")
    raw=path.read_bytes()
    digest.update(len(raw).to_bytes(8,"big"))
    digest.update(raw)
    for name in sorted(artifact_hashes):
        encoded=name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4,"big"))
        digest.update(encoded)
        digest.update(artifact_hashes[name])
    return digest.hexdigest()

def marker(job_id):
    try:
        x=_read_json(REQUESTS/(job_id+".json"),4096)
        if type(x) is not dict or set(x)!={"parent_job_id","successor_job_id",
                                          "handoff_sha256","manifest_sha256","terminal"}:
            return None
        if any(type(x[k]) is not str or not SHA.fullmatch(x[k])
               for k in ("handoff_sha256","manifest_sha256")):
            return None
        if (type(x["terminal"]) is not bool or
            type(x["parent_job_id"]) is not str or
            type(x["successor_job_id"]) is not str):
            return None
        return x
    except (OSError,ContinuationError):return None

def request_valid(lane,generation,role,tickets,seed_count,digest):
    id=jid(lane,generation,role)
    req=marker(id)
    if not req:return False
    next_lane,next_generation,next_role=successor(lane,generation,role)
    expected="" if req["terminal"] else jid(next_lane,next_generation,next_role)
    try:
        sha=handoff_sha(lane,generation,role,tickets,seed_count)
        handoff=_read_json(handoff_path(lane,generation,role),20000)
        actual_terminal=(role=="reviewer" and handoff["no_actionable_work"] is True)
    except (OSError,ValueError,KeyError,TypeError,ContinuationError,QualityError):return False
    return (req["parent_job_id"]==id and req["successor_job_id"]==expected
            and req["manifest_sha256"]==digest
            and req["terminal"] is actual_terminal
            and req["handoff_sha256"]==sha)

def request(parent_id):
    cfg,tickets,old_digest,digest=load()
    if not armed(digest):raise ContinuationError("campaign_not_owner_armed")
    if type(parent_id) is not str:
        raise ContinuationError("invalid_parent_id")
    state,obj=previous.locate(parent_id)
    if state!="active":raise ContinuationError("parent_not_active")
    metadata=obj.get("role_metadata") or {}
    if (type(metadata) is not dict or metadata.get("campaign_id")!="chain3"
        or metadata.get("manifest_sha256")!=digest
        or metadata.get("stage") not in ROLES):
        raise ContinuationError("parent_campaign_mismatch")
    lane,generation=parse_label(metadata.get("ticket_id"),cfg["seed_count"])
    role=metadata["stage"]
    if jid(lane,generation,role)!=parent_id:
        raise ContinuationError("parent_identity_mismatch")
    h=handoff_sha(lane,generation,role,tickets,cfg["seed_count"])
    contents=_read_json(handoff_path(lane,generation,role))
    terminal=contents["no_actionable_work"] is True and role=="reviewer"
    next_lane,next_gen,next_role=successor(lane,generation,role)
    child=None if terminal else jid(next_lane,next_gen,next_role)
    payload={"parent_job_id":parent_id,"successor_job_id":child or "",
             "handoff_sha256":h,"manifest_sha256":digest,"terminal":terminal}
    REQUESTS.mkdir(parents=True,exist_ok=True)
    path=REQUESTS/(parent_id+".json")
    if path.exists():
        if marker(parent_id)!=payload:raise ContinuationError("immutable_request_conflict")
        return {"ok":True,"already_requested":True,"successor":child,
                "queued":False,"terminal":terminal}
    try:
        with path.open("x",encoding="utf-8") as f:
            json.dump(payload,f);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if marker(parent_id)!=payload:raise ContinuationError("immutable_request_conflict")
        return {"ok":True,"already_requested":True,"successor":child,
                "queued":False,"terminal":terminal}
    return {"ok":True,"requested":True,"successor":child,
            "queued":False,"terminal":terminal}

def accepted(lane,generation,role,cfg,tickets,digest):
    state,record=locate(lane,generation,role)
    if state!="done" or record.get("status")!="done":return False
    if not request_valid(lane,generation,role,tickets,cfg["seed_count"],digest):
        return False
    id=jid(lane,generation,role)
    report=REPORTS/("completion-"+id+".md")
    receipt=ACKS/(id+".json")
    try:
        if report.is_symlink() or receipt.is_symlink():return False
        ack=_read_json(receipt,4096)
        if not report.is_file() or report.stat().st_size>100000:return False
        actual_sha=hashlib.sha256(report.read_bytes()).hexdigest()
    except (OSError,ContinuationError):return False
    return (ack.get("job_id")==id and ack.get("status")=="REPORT_ACKED"
        and actual_sha==ack.get("artifact_sha256")==record.get("report_sha256")
        and ack.get("ack_id")==record.get("report_ack_id")
        and record.get("role_metadata")=={"campaign_id":"chain3",
            "ticket_id":label(lane,generation),"stage":role,
            "manifest_sha256":digest})

def prerequisite(lane,generation,role,cfg,tickets,digest):
    p=predecessor(lane,generation,role,tickets,cfg["seed_count"])
    if p[0]=="chain2":
        if not chain2_finished():return False
        oldcfg,oldtickets,olddigest=previous.load()
        return previous.accepted(p[1],"reviewer",olddigest,oldtickets,oldcfg["seed_count"])
    return accepted(p[1],p[2],p[3],cfg,tickets,digest) and not (
        marker(jid(p[1],p[2],p[3])) or {}).get("terminal",False)

def ready(cfg,tickets,digest):
    if not chain2_finished():return
    for lane in range(cfg["seed_count"]):
        generation=0
        while True:
            dev,rev=locate(lane,generation,"developer"),locate(lane,generation,"reviewer")
            if dev[0] is None:
                if prerequisite(lane,generation,"developer",cfg,tickets,digest):
                    yield lane,generation,"developer"
                break
            if rev[0] is None:
                if prerequisite(lane,generation,"reviewer",cfg,tickets,digest):
                    yield lane,generation,"reviewer"
                break
            if dev[0]!="done" or rev[0]!="done":break
            # Do not cross a terminated or unaccepted reviewer.
            if not accepted(lane,generation,"reviewer",cfg,tickets,digest):
                break
            if marker(jid(lane,generation,"reviewer"))["terminal"]:
                break
            generation+=1

def ensure_brief(lane,generation,role,cfg,tickets,digest):
    """Immutable English/ASCII brief with a concrete reviewed issue if any.

    The source file stays UTF-8 regardless of Windows terminal code page.
    """
    name=role_name(lane,generation,role)
    job=jid(lane,generation,role)
    upstream=upstream_path(lane,generation,role,tickets,cfg["seed_count"])
    folder=(f"chat-launch-smoke-desktop-test/experiments/role-workstreams/"
            f"chain3/{label(lane,generation)}/{role}/")
    source_ticket=last_chain2_ticket(lane,tickets,cfg["seed_count"])
    approved_ticket=next(t for t in tickets if t["id"]==source_ticket)
    active_work=None
    if role=="developer" and generation>0:
        previous_review=_read_json(handoff_path(lane,generation-1,"reviewer"),20000)
        if previous_review.get("no_actionable_work") is True:
            raise ContinuationError("work_exhausted")
        active_work=previous_review.get("next_work")
        if type(active_work) is not dict:
            raise ContinuationError("previous_review_missing_verified_work")
    if role=="developer":
        task=("Implement or correct only previously approved scope with "
              "a reproducible test, and record exact results.")
    else:
        task=("Independently rerun tests, verify the previous implementation, "
              "and document genuine regressions or close the lane.")
    work_info=(json.dumps(active_work,ensure_ascii=True,sort_keys=True)
               if active_work is not None else
               "First scoped verification of the approved SDD criteria; "
               "do not invent defects.")
    text=(
        "# Chain3: evidence-driven successor chain\n"
        f"Job ID: {job}\nRole: {role}\nLane: {lane}\nGeneration: {generation}\n"
        f"Source approved Chain2 ticket: {source_ticket}\n"
        f"Approved goal: {approved_ticket['goal']}\n"
        f"Acceptance criteria: {json.dumps(approved_ticket['acceptance'],ensure_ascii=True)}\n"
        f"Negative scenarios: {json.dumps(approved_ticket['failure_scenarios'],ensure_ascii=True)}\n"
        "Connect Local Workspace MCP; list_workspaces alias projects.\n"
        f"Read predecessor handoff before working: {upstream}\n"
        f"Scoped assignment: {task}\n"
        f"Verified issue to address: {work_info}\n"
        f"Change files ONLY under: {folder}\n"
        "Read other role folders without writing. No Git/merge/deploy, "
        "production changes, live SQLite, privileged credentials, unapproved "
        "features, external payments or account automation bypass.\n"
        "At the END (not startup), write exactly one handoff.json in your role "
        "folder with: version=1, parent_job_id equal to this Job ID, "
        "upstream_handoff equal to the exact predecessor path, summary (15+), "
        "changed_files listing actual relative files, tests (observed results), "
        "issues, feature_proposals (review-only), next_focus (15+), "
        "no_actionable_work (boolean).\n"
        "Developer MUST set no_actionable_work=false and no next_work. "
        "Reviewer with no meaningful further issue MUST set "
        "no_actionable_work=true and next_work=null.\n"
        "Reviewer with no_actionable_work=false MUST add a structured "
        "next_work object with EXACT fields: issue_id (new unique slug), "
        "criterion (verbatim existing SDD acceptance/failure/validation), "
        "objective (30+ chars), evidence_file (.txt/.json/.md, included in "
        "changed_files, >=80 bytes), evidence_sha256 (exact SHA256 of file), "
        "next_verification (30+ chars). Do not recycle issue_id; reusing "
        "an old issue or inventing new feature scope blocks further work.\n"
        "This evidence is advisory, not a trusted owner approval. "
        "Only trusted user review can authorize production changes.\n"
        f"Call Local Workspace run_script once with workspace=projects, "
        f"path=chat-launch-smoke-desktop-test/web-companion/chain3_continuous.py, "
        f"cwd=chat-launch-smoke-desktop-test/web-companion, "
        f"args=['request','--parent','{job}']; check job_status completed rc=0, "
        "then job_output ok=true. This records one successor request ONLY, "
        "not a new browser chat or a self-granted permission.\n"
        f"Then write exactly one report: agent-reports/completion-{job}.md "
        "with Job ID and headings ## Outcome, ## Evidence, ## Tests, "
        "## Handoff. Do not claim tests PASS without executing them.\n"
        "Do not invent defects. Do not recycle unresolved issues as new work. "
        "Continue only when verified useful work remains.\n"
    )
    if len(text.encode("utf-8"))>32768:
        raise ContinuationError("brief_too_large")
    path=BRIEFS/(name+".md")
    BRIEFS.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_text(encoding="utf-8")!=text:
            raise ContinuationError("immutable_brief_conflict")
    else:
        with path.open("x",encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    return "agent-briefs/"+name+".md"

def rate_state():
    try:
        d=_read_json(RATE,4096)
        if type(d) is not dict or set(d)!={"utc_day","claims","last_claim_at"}:
            raise ContinuationError("rate_state_invalid")
        if type(d["claims"]) is not int or d["claims"]<0:
            raise ContinuationError("rate_state_invalid")
        return d
    except (OSError,ContinuationError):
        if RATE.exists():raise
        return {"utc_day":"","claims":0,"last_claim_at":""}

def rate_ok(cfg):
    state=rate_state()
    now=datetime.now(timezone.utc)
    day=now.strftime("%Y-%m-%d")
    if state["utc_day"]==day and state["claims"]>=cfg["max_claims_per_utc_day"]:
        return False
    if state["last_claim_at"]:
        try:
            last=datetime.fromisoformat(state["last_claim_at"])
            if (now-last).total_seconds()<cfg["min_claim_interval_seconds"]:
                return False
        except ValueError:raise ContinuationError("rate_timestamp_invalid")
    return True

def reserve_claim(job):
    cfg,tickets,old_digest,digest=load()
    if not claim_allowed(job):return False
    now=datetime.now(timezone.utc)
    old=rate_state()
    day=now.strftime("%Y-%m-%d")
    next_state={"utc_day":day,"claims":(old["claims"]+1 if old["utc_day"]==day else 1),
                "last_claim_at":now.isoformat()}
    RATE.parent.mkdir(parents=True,exist_ok=True)
    tmp=RATE.with_suffix(".writing")
    with tmp.open("x",encoding="utf-8") as f:
        json.dump(next_state,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,RATE)
    return True

def refill_unlocked():
    cfg,tickets,old_digest,digest=load()
    if HOLD.exists():return {"ok":True,"queued":0,"reason":"admission_held"}
    if not mode_can_run():return {"ok":True,"queued":0,"reason":"trusted_host_attestation_required"}
    if not armed(digest):return {"ok":True,"queued":0,"reason":"not_owner_armed"}
    if not chain2_finished():return {"ok":True,"queued":0,"reason":"chain2_incomplete"}
    if not legacy.client_ready():return {"ok":True,"queued":0,"reason":"browser_not_ready"}
    existing_queue=list(QUEUE.glob("*.json"))
    existing_active=list(ACTIVE.glob("*.json"))
    space=min(cfg["max_queued"]-len(existing_queue),
              cfg["max_active"]-len(existing_active)-len(existing_queue),
              25-len(existing_active)-len(existing_queue))
    if space<=0:return {"ok":True,"queued":0,"reason":"capacity"}
    result=[]
    for lane,generation,role in ready(cfg,tickets,digest):
        if len(result)>=space:break
        id=jid(lane,generation,role)
        brief=ensure_brief(lane,generation,role,cfg,tickets,digest)
        metadata={"campaign_id":"chain3","ticket_id":label(lane,generation),
                  "stage":role,"manifest_sha256":digest}
        queued=enqueue(role_name(lane,generation,role),brief,job_id=id,metadata=metadata)
        if not queued.get("already_exists"):
            result.append({"lane":lane,"generation":generation,"role":role,"job_id":id})
    return {"ok":True,"queued":len(result),"jobs":result}

def claim_allowed(job):
    try:
        cfg,tickets,old_digest,digest=load()
        meta=job.get("role_metadata") or {}
        if not armed(digest) or HOLD.exists() or not chain2_finished() or not legacy.client_ready():
            return False
        if type(meta) is not dict or set(meta)!={"campaign_id","ticket_id","stage","manifest_sha256"}:
            return False
        if (meta["campaign_id"]!="chain3" or meta["manifest_sha256"]!=digest
            or meta["stage"] not in ROLES or job.get("completion_mode")!="report"):
            return False
        lane,generation=parse_label(meta["ticket_id"],cfg["seed_count"])
        role=meta["stage"]
        id=jid(lane,generation,role)
        brief=BRIEFS/(role_name(lane,generation,role)+".md")
        if (job.get("id")!=id or job.get("role")!=role_name(lane,generation,role)
            or job.get("report_name")!="completion-"+id+".md" or
            not brief.is_file() or brief.is_symlink()
            or hashlib.sha256(brief.read_bytes()).hexdigest()!=job.get("brief_sha256")):
            return False
        if not prerequisite(lane,generation,role,cfg,tickets,digest):
            return False
        # Enforce all campaign/global active limits, even if queuer is buggy.
        if len(list(ACTIVE.glob("*.json")))>=cfg["max_active"]:return False
        return rate_ok(cfg)
    except (OSError,ValueError,KeyError,TypeError,ContinuationError):
        return False

def handoff_requested_for_job(job):
    meta=job.get("role_metadata") or {}
    if meta.get("campaign_id")!="chain3":return False
    try:
        cfg,tickets,old_digest,digest=load()
        if not armed(digest):return False
        lane,generation=parse_label(meta["ticket_id"],cfg["seed_count"])
        role=meta["stage"]
        return (meta["manifest_sha256"]==digest and
            job.get("id")==jid(lane,generation,role) and
            request_valid(lane,generation,role,tickets,cfg["seed_count"],digest))
    except (OSError,ValueError,KeyError,TypeError,ContinuationError):return False

def preview():
    cfg,tickets,old_digest,digest=load()
    try:finished=chain2_finished()
    except (OSError,ValueError,TypeError,previous.ChainError):finished=False
    active=sum(1 for _ in ACTIVE.glob("*.json"))
    done=sum(1 for p in DONE.glob("*.json") if _is_chain3_record(p))
    proposed=[]
    if finished:
        proposed=[{"lane":lane,"generation":gen,"role":role}
                  for lane,gen,role in ready(cfg,tickets,digest)]
    return {"ok":True,"mode":"READ_ONLY","campaign":"chain3",
            **mode_status(),"generation_limit":None,"owner_armed":armed(digest),
            "chain2_fully_accepted":finished,"admission_held":HOLD.exists(),
            "active_global":active,"done_chain3":done,"eligible_successors":proposed,
            "max_active":cfg["max_active"],"max_queued":cfg["max_queued"],
            "min_claim_interval_seconds":cfg["min_claim_interval_seconds"],
            "max_claims_per_utc_day":cfg["max_claims_per_utc_day"],
            "no_jobs_created":True}

def _is_chain3_record(path):
    try:
        d=_read_json(path)
        return (d.get("role_metadata") or {}).get("campaign_id")=="chain3"
    except (OSError,ContinuationError,AttributeError):return False

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("preview","arm","request","mode","select-mode"))
    p.add_argument("--confirm-owner-arm",action="store_true")
    p.add_argument("--confirm-mode-selection",action="store_true")
    p.add_argument("--mode",choices=MODES)
    p.add_argument("--parent")
    a=p.parse_args()
    try:
        if a.action=="preview":out=preview()
        elif a.action=="mode":out={"ok":True,"mode":"READ_ONLY",**mode_status()}
        elif a.action=="select-mode":out=select_mode(a.mode,a.confirm_mode_selection)
        elif a.action=="arm":out=arm(a.confirm_owner_arm)
        else:out=request(a.parent)
    except (OSError,ValueError,KeyError,TypeError,ContinuationError) as e:
        out={"ok":False,"error":str(e)}
    print(json.dumps(out,ensure_ascii=False))
    if not out.get("ok"):raise SystemExit(2)
if __name__=="__main__":main()
