"""Two-role finite handoff chain. No role asks for a successor at startup.

Agent requests exactly one successor via Local Workspace run_script at END;
the server queues that successor only after the parent is DONE with a valid
report ACK and an immutable, verified handoff. Owner-arm, cap and hold apply.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,uuid
from datetime import datetime,timezone
from pathlib import Path
from enqueue_completion import enqueue
import role_campaign as legacy

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
STATE=ROOT/"bridge-state"
QUEUE,ACTIVE,DONE=(STATE/n for n in ("queue","active","done"))
ACKS,REPORTS=STATE/"completion-acks",PROJECT/"agent-reports"
BRIEFS=PROJECT/"agent-briefs"
HOLD,ARM=STATE/"admission-hold.json",STATE/"role-campaign-armed.json"
CONFIG=ROOT/"role-campaigns"/"chain2.json"
REQUESTS=STATE/"handoff-requests"
ROLES=("developer","reviewer")
HEX=re.compile(r"[0-9a-f]{64}\Z")
UUID=re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z")

class ChainError(RuntimeError):pass

def load():
    if CONFIG.is_symlink() or CONFIG.stat().st_size>5000:raise ChainError("unsafe_config")
    raw=CONFIG.read_bytes(); cfg=json.loads(raw)
    keys={"schema_version","campaign_id","source_manifest","seed_count","max_queued","max_active","requires_owner_arm","roles","delivery","features_require_review"}
    if type(cfg) is not dict or set(cfg)!=keys:raise ChainError("invalid_config")
    if (cfg["schema_version"]!=1 or cfg["campaign_id"]!="chain2" or cfg["source_manifest"] not in ("eng1.json","sdd1.json")
        or cfg["roles"]!=list(ROLES) or cfg["requires_owner_arm"] is not True
        or cfg["delivery"]!="handoff_request_end_only" or cfg["features_require_review"] is not True
        or type(cfg["seed_count"]) is not int or not 1<=cfg["seed_count"]<=12
        or type(cfg["max_queued"]) is not int or not 1<=cfg["max_queued"]<=12
        or type(cfg["max_active"]) is not int or not 1<=cfg["max_active"]<=25):
        raise ChainError("unsafe_config")
    source,source_sha=legacy.load(cfg["source_manifest"][:-5])
    tickets=sorted(source["tickets"],key=lambda t:(-t["priority"],t["id"]))
    digest=hashlib.sha256(raw+source_sha.encode("ascii")).hexdigest()
    return cfg,tickets,digest

def jid(ticket,role):
    return legacy.stage_id("chain2",ticket,role)

def role_name(ticket,role):
    return legacy.role_name("chain2",ticket,role)

def locate(id):
    files=[(label,p/(id+".json")) for label,p in (("queue",QUEUE),("active",ACTIVE),("done",DONE))
           if (p/(id+".json")).is_file()]
    if len(files)>1:raise ChainError("duplicate_state")
    if not files:return None,None
    state,path=files[0]
    obj=json.loads(path.read_text(encoding="utf-8"))
    if obj.get("id")!=id:raise ChainError("identity_mismatch")
    return state,obj

def successor(ticket,role,tickets,seed_count):
    ids=[t["id"] for t in tickets]
    idx=ids.index(ticket)
    if role=="developer":return ticket,"reviewer"
    nxt=idx+seed_count
    return (ids[nxt],"developer") if nxt<len(ids) else (None,None)

def parent_for(ticket,role,tickets,seed_count):
    ids=[t["id"] for t in tickets]
    if role=="reviewer":return ticket,"developer"
    idx=ids.index(ticket)
    if idx<seed_count:return None,None
    return ids[idx-seed_count],"reviewer"

def workspace(ticket,role):
    return PROJECT/"experiments"/"role-workstreams"/"chain2"/ticket/role

def handoff_path(ticket,role):
    return workspace(ticket,role)/"handoff.json"

def read_handoff(id,ticket,role):
    path=handoff_path(ticket,role)
    if not path.is_file() or path.is_symlink() or not 100<=path.stat().st_size<=20000:
        raise ChainError("handoff_missing_or_invalid")
    raw=path.read_bytes();data=json.loads(raw)
    fields={"version","parent_job_id","upstream_handoff","summary","changed_files","tests","issues","feature_proposals","next_focus"}
    optional={"source_sha256","files_sha256","production_authorization_ready",
              "owner_approval_obtained","canonical_code_changed","live_sqlite_changed"}
    if (type(data) is not dict or not fields.issubset(data) or
        not set(data).issubset(fields|optional) or
        data["version"]!=1 or data["parent_job_id"]!=id):
        raise ChainError("handoff_schema_mismatch")
    for flag in ("production_authorization_ready","owner_approval_obtained",
                 "canonical_code_changed","live_sqlite_changed"):
        if flag in data and data[flag] is not False:
            raise ChainError("untrusted_authority_claim")
    for key in ("source_sha256","files_sha256"):
        if key in data:
            mapping=data[key]
            if (type(mapping) is not dict or len(mapping)>40 or
                any(type(k) is not str or not 1<=len(k)<=120
                    or type(v) is not str or not HEX.fullmatch(v)
                    for k,v in mapping.items())):
                raise ChainError("handoff_invalid_sha_metadata")
    cfg,tickets,_=load()
    prev_ticket,prev_role=parent_for(ticket,role,tickets,cfg["seed_count"])
    upstream=(f"chat-launch-smoke-desktop-test/experiments/role-workstreams/chain2/"
              f"{prev_ticket}/{prev_role}/handoff.json" if prev_ticket else None)
    if data["upstream_handoff"]!=upstream:
        raise ChainError("handoff_upstream_mismatch")
    for field in ("summary","next_focus"):
        val=data[field]
        if type(val) is not str or not 10<=len(val.strip())<=2000:raise ChainError("handoff_weak_text")
    for field,limit in (("changed_files",30),("tests",40),("issues",30),("feature_proposals",20)):
        arr=data[field]
        if type(arr) is not list or len(arr)>limit:
            raise ChainError("handoff_invalid_list")
        for entry in arr:
            if field in ("changed_files","issues"):
                if type(entry) is not str or not 3<=len(entry)<=350:
                    raise ChainError("handoff_invalid_list")
                continue
            # Test receipts and feature proposals are advisory data, not permissions.
            # Accept bounded structured evidence without accepting an identity claim.
            if type(entry) is str and 3<=len(entry)<=350:
                continue
            if (type(entry) is not dict or not 1<=len(entry)<=30 or
                any(type(k) is not str or not re.fullmatch(r"[A-Za-z0-9_]{1,48}",k)
                    for k in entry)):
                raise ChainError("handoff_invalid_list")
            try:
                raw_entry=json.dumps(entry,allow_nan=False,ensure_ascii=False)
            except (TypeError,ValueError):
                raise ChainError("handoff_invalid_list")
            def bounded_advisory_value(value):
                return ((type(value) in (int,float,bool,type(None))) or
                    (type(value) is str and len(value)<=800) or
                    (type(value) is list and len(value)<=20 and
                     all(type(item) is str and 1<=len(item)<=350 for item in value)))
            if len(raw_entry.encode("utf-8"))>2400 or any(
                not bounded_advisory_value(v) for v in entry.values()):
                raise ChainError("handoff_invalid_list")
    # Handoff references are textual, never executable instructions.
    if not data["changed_files"] or not data["tests"]:
        raise ChainError("handoff_missing_evidence")
    root=workspace(ticket,role).resolve()
    approved=(PROJECT/"experiments"/"role-workstreams"/"chain2").resolve()
    if not root.is_relative_to(approved) or workspace(ticket,role).is_symlink():
        raise ChainError("handoff_workspace_escape")
    for name in data["changed_files"]:
        relative=Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ChainError("unsafe_artifact_path")
        artifact=root/relative
        if not artifact.is_file() or artifact.is_symlink() or not artifact.resolve().is_relative_to(root):
            raise ChainError("unverified_handoff_artifact")
    for name,expected_hash in data.get("files_sha256",{}).items():
        if name not in data["changed_files"]:
            raise ChainError("handoff_hash_unlisted_file")
        candidate=root/name
        if (not candidate.is_file() or candidate.is_symlink() or
            not candidate.resolve().is_relative_to(root) or
            hashlib.sha256(candidate.read_bytes()).hexdigest()!=expected_hash):
            raise ChainError("handoff_artifact_sha_mismatch")
    return hashlib.sha256(raw).hexdigest()

def marker(id):
    path=REQUESTS/(id+".json")
    if not path.is_file() or path.is_symlink():return None
    try:
        x=json.loads(path.read_text(encoding="utf-8"))
        if type(x) is not dict or set(x)!={"parent_job_id","successor_job_id","handoff_sha256","campaign_digest"}:
            return None
        if not HEX.fullmatch(x["handoff_sha256"]) or not HEX.fullmatch(x["campaign_digest"]):
            return None
        return x
    except (ValueError,OSError,TypeError,KeyError):return None

def request_valid(ticket,role,digest,tickets,seed_count):
    id=jid(ticket,role)
    req=marker(id)
    if not req:return False
    expected,_role=successor(ticket,role,tickets,seed_count)
    child=jid(expected,_role) if expected else ""
    if (req["parent_job_id"]!=id or req["successor_job_id"]!=child
        or req["campaign_digest"]!=digest):
        return False
    try:return read_handoff(id,ticket,role)==req["handoff_sha256"]
    except (OSError,ValueError,ChainError):return False

def accepted(ticket,role,digest,tickets,seed_count):
    state,data=locate(jid(ticket,role))
    if state!="done" or data.get("status")!="done" or not request_valid(ticket,role,digest,tickets,seed_count):
        return False
    id=jid(ticket,role)
    report,ackfile=REPORTS/("completion-"+id+".md"),ACKS/(id+".json")
    if not report.is_file() or report.is_symlink() or not ackfile.is_file() or ackfile.is_symlink():
        return False
    try:
        ack=json.loads(ackfile.read_text(encoding="utf-8"))
        h=hashlib.sha256(report.read_bytes()).hexdigest()
    except (OSError,ValueError):return False
    return (ack.get("job_id")==id and ack.get("status")=="REPORT_ACKED"
        and h==ack.get("artifact_sha256")==data.get("report_sha256")
        and ack.get("ack_id")==data.get("report_ack_id")
        and data.get("role_metadata")=={"campaign_id":"chain2","ticket_id":ticket,
             "stage":role,"manifest_sha256":digest})

def armed(digest,number):
    try:
        d=json.loads(ARM.read_text(encoding="utf-8"))
        return (d.get("enabled") is True and d.get("campaign_id")=="chain2" and
                d.get("manifest_sha256")==digest and d.get("max_total_stages")==2*number)
    except (OSError,ValueError,TypeError):return False

def arm(confirm=False):
    cfg,tickets,digest=load()
    if not confirm:raise ChainError("explicit_owner_arm_required")
    if HOLD.is_file():raise ChainError("admission_held")
    if any(QUEUE.glob("*.json")) or any(ACTIVE.glob("*.json")):
        raise ChainError("existing_jobs_require_reconciliation")
    if not legacy.client_ready():raise ChainError("browser_not_ready")
    if ARM.exists():
        if armed(digest,len(tickets)):return {"ok":True,"already_armed":True}
        raise ChainError("arm_conflict")
    ARM.parent.mkdir(parents=True,exist_ok=True)
    with ARM.open("x",encoding="utf-8") as f:
        json.dump({"enabled":True,"campaign_id":"chain2",
                   "manifest_sha256":digest,"max_total_stages":2*len(tickets),
                   "armed_at":datetime.now(timezone.utc).isoformat()},f)
    return {"ok":True,"armed":True,"features":len(tickets)}

def request(parent):
    """Called by the actual agent via Local Workspace MCP run_script near end."""
    if type(parent) is not str or not UUID.fullmatch(parent):
        raise ChainError("invalid_parent")
    cfg,tickets,digest=load()
    # Operational hold blocks NEW browser claims, not immutable handoff
    # requests by an already active, manifest-bound stage.
    if not armed(digest,len(tickets)):
        raise ChainError("campaign_not_authorized")
    active,record=locate(parent)
    if active!="active":raise ChainError("parent_not_active")
    m=record.get("role_metadata") or {}
    if (m.get("campaign_id")!="chain2" or m.get("manifest_sha256")!=digest
        or m.get("stage") not in ROLES or m.get("ticket_id") not in {t["id"] for t in tickets}
        or parent!=jid(m["ticket_id"],m["stage"])):
        raise ChainError("parent_not_owned")
    ticket,role=m["ticket_id"],m["stage"]
    h=read_handoff(parent,ticket,role)
    child_ticket,child_role=successor(ticket,role,tickets,cfg["seed_count"])
    child_id=jid(child_ticket,child_role) if child_ticket else ""
    doc={"parent_job_id":parent,"successor_job_id":child_id,
         "handoff_sha256":h,"campaign_digest":digest}
    REQUESTS.mkdir(parents=True,exist_ok=True)
    dest=REQUESTS/(parent+".json")
    if dest.exists():
        old=marker(parent)
        if old!=doc:raise ChainError("immutable_request_conflict")
        return {"ok":True,"already_requested":True,"successor":child_id or None}
    try:
        with dest.open("x",encoding="utf-8") as f:
            json.dump(doc,f)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        if marker(parent)!=doc:raise ChainError("immutable_request_conflict")
        return {"ok":True,"already_requested":True,"successor":child_id or None}
    return {"ok":True,"requested":True,"successor":child_id or None,
            "queued":False,"note":"successor admitted only after parent DONE and ACK"}

def brief_text(ticket,role,job,parent):
    output=f"chat-launch-smoke-desktop-test/experiments/role-workstreams/chain2/{ticket['id']}/{role}/"
    source=(f"chat-launch-smoke-desktop-test/experiments/role-workstreams/chain2/{parent[0]}/{parent[1]}/handoff.json"
            if parent[0] else None)
    title="УНИВЕРСАЛЬНЫЙ РАЗРАБОТЧИК" if role=="developer" else "НЕЗАВИСИМЫЙ РЕВЬЮЕР"
    review_step=("Отдельно рассмотри каждую запись feature_proposals из предыдущего handoff: "
                 "оцени необходимость, риск, тестируемость, выдай ACCEPT_FOR_PROPOSAL_REVIEW "
                 "или REJECT_PROPOSAL с аргументами. Не запускай предложенные фичи. "
                 "Сформулируй предложения в собственном handoff для рассмотрения человеком."
                 if role=="reviewer" else "")
    work_step=("Спроектируй и реализуй полноценную фичу: дизайн, код, реальные тесты, "
               "негативные сценарии и README." if role=="developer" else
               "Выполни независимое содержательное ревью: запусти тесты разработчика, "
               "создай собственные негативные и регрессионные тесты, задокументируй "
               "найденные дефекты и доказательства, выдай ACCEPT/REWORK/BLOCKED.")
    return f"""# {title}: {ticket['title']}
Job ID: {job}
Role: {role}
Ticket: {ticket['id']}

## Большая инженерная задача
{ticket['goal']}
{review_step}
{work_step}
Глубина результата важнее искусственной длительности.
""" + (f"## Обязательный контекст предшественника\nПрочитай через MCP: {source}\n"
          "Прочитай упомянутые там файлы и результаты; не начинай с нуля.\n"
          if source else "## Начальное задание\nЭто корневое задание кампании; предшественника нет.\n") + f"""
## Критерии приемки
{legacy.bullets(ticket['acceptance'])}
## Отрицательные сценарии
{legacy.bullets(ticket['failure_scenarios'])}
## Контракты
{legacy.bullets(ticket['interfaces'])}
## Проверка
{legacy.bullets(ticket['validation'])}

## Права
Изменяй файлы только в {output}
Соседние роли читай, но не изменяй. Не трогай основной код,
аккаунт, Git, live SQLite, старую очередь и чужие проекты.
Не делай никаких новых агентов В НАЧАЛЕ.

## Один преемник В КОНЦЕ КАЖДОГО ЗВЕНА
Когда приближаешься к завершению текущей работы, резервируй заключительный этап на доказательства, handoff и заявку преемнику. Если работа не окончена, честно опиши незавершённое в issues и next_focus — не выдумывай PASS.
Никогда не делай запрос преемника в начале или ради искусственной длительности. По завершении собственной полезной работы создай {output}handoff.json
через Local Workspace write_file. Поля JSON: version=1,
parent_job_id="{job}", upstream_handoff={json.dumps(source,ensure_ascii=False)},
summary (10+ символов), changed_files
(список относительных имён созданных файлов), tests (проверенные результаты),
issues (блокеры), feature_proposals (новые идеи на ревью, не на запуск),
next_focus (конкретное направление преемнику).
Потом ровно один раз (повтор только при неизвестном ответе с тем же id):
Local Workspace run_script workspace=projects,
path=chat-launch-smoke-desktop-test/web-companion/chain_campaign.py,
cwd=chat-launch-smoke-desktop-test/web-companion,
args=["request","--parent","{job}"].
Проверь job_status completed, returncode=0, job_output ok=true.
Это создаёт заявку ровно на ОДНОГО преемника, а НЕ браузерную вкладку.
Преемник стартует только ПОСЛЕ итогового серверного DONE родителя.
Если заранее утверждённый backlog исчерпан, ответ будет successor=null.
Нельзя самостоятельно вызывать enqueue.py или открывать браузерный чат.
В самом конце запиши отчёт в уникальный путь launcher: Job ID: {job},
## Outcome, ## Evidence, ## Tests, ## Handoff. Не пиши отчёт до
проверенного запроса преемника, иначе browser gate остановит финализацию.
Предлагай фичи в handoff и итоговом отчёте — ревьюер рассматривает их,
но автоматического одобрения/постановки этих фич в очередь нет.
"""

def ensure_brief(ticket,role,job,parent):
    name=role_name(ticket["id"],role)
    path=BRIEFS/(name+".md")
    body=brief_text(ticket,role,job,parent)
    if len(body.encode("utf-8"))>32768:raise ChainError("brief_too_large")
    BRIEFS.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_text(encoding="utf-8")!=body:
            raise ChainError("brief_conflict")
    else:
        with path.open("x",encoding="utf-8") as f:f.write(body)
    return "agent-briefs/"+path.name

def prior_ok(ticket,role,tickets,cfg,digest):
    parent,previous=parent_for(ticket,role,tickets,cfg["seed_count"])
    return parent is None or accepted(parent,previous,digest,tickets,cfg["seed_count"])

def ready(cfg,tickets,digest):
    for i,t in enumerate(tickets):
        for role in ROLES:
            if locate(jid(t["id"],role))[0] is not None:continue
            if role=="developer" and i<cfg["seed_count"]:
                yield t,role,(None,None)
                continue
            parent=parent_for(t["id"],role,tickets,cfg["seed_count"])
            if prior_ok(t["id"],role,tickets,cfg,digest):
                yield t,role,parent

def refill_unlocked():
    cfg,tickets,digest=load()
    if HOLD.exists():return {"ok":True,"queued":0,"reason":"admission_held"}
    if not armed(digest,len(tickets)):return {"ok":True,"queued":0,"reason":"not_armed"}
    if not legacy.client_ready():return {"ok":True,"queued":0,"reason":"browser_not_ready"}
    pending=list(QUEUE.glob("*.json")); active=list(ACTIVE.glob("*.json"))
    for f in pending+active:
        obj=json.loads(f.read_text(encoding="utf-8"))
        if (obj.get("role_metadata") or {}).get("campaign_id")!="chain2":
            return {"ok":True,"queued":0,"reason":"legacy_jobs_present"}
    space=min(cfg["max_queued"]-len(pending),25-len(pending),
              cfg["max_active"]-len(active)-len(pending))
    if space<=0:return {"ok":True,"queued":0,"reason":"capacity"}
    out=[]
    for ticket,role,parent in ready(cfg,tickets,digest):
        if len(out)>=space:break
        id=jid(ticket["id"],role)
        brief=ensure_brief(ticket,role,id,parent)
        meta={"campaign_id":"chain2","ticket_id":ticket["id"],
              "stage":role,"manifest_sha256":digest}
        result=enqueue(role_name(ticket["id"],role),brief,job_id=id,metadata=meta)
        if not result.get("already_exists"):
            out.append({"ticket":ticket["id"],"role":role,"job_id":id})
    return {"ok":True,"queued":len(out),"jobs":out}

def claim_allowed(job):
    meta=job.get("role_metadata") or {}
    if set(meta)!={"campaign_id","ticket_id","stage","manifest_sha256"}:return False
    try:
        cfg,tickets,digest=load()
        ticket=next(t for t in tickets if t["id"]==meta["ticket_id"])
        role=meta["stage"]
        if (meta["campaign_id"]!="chain2" or role not in ROLES
            or meta["manifest_sha256"]!=digest or not armed(digest,len(tickets))
            or HOLD.exists() or not legacy.client_ready()
            or job.get("id")!=jid(ticket["id"],role)
            or job.get("role")!=role_name(ticket["id"],role)
            or job.get("completion_mode")!="report"
            or job.get("report_name")!="completion-"+job["id"]+".md"):
            return False
        brief=BRIEFS/(job["role"]+".md")
        return (brief.is_file() and not brief.is_symlink() and
                hashlib.sha256(brief.read_bytes()).hexdigest()==job.get("brief_sha256")
                and prior_ok(ticket["id"],role,tickets,cfg,digest))
    except (OSError,ValueError,KeyError,TypeError,ChainError):return False

def handoff_requested_for_job(job):
    m=job.get("role_metadata") or {}
    if m.get("campaign_id")!="chain2":return True
    try:
        cfg,tickets,digest=load()
        return (m.get("manifest_sha256")==digest and
                request_valid(m["ticket_id"],m["stage"],digest,tickets,cfg["seed_count"]))
    except (OSError,ValueError,KeyError,TypeError,ChainError):return False

def preview():
    cfg,tickets,digest=load()
    counts={"queue":0,"active":0,"done":0}
    for t in tickets:
        for role in ROLES:
            state,_=locate(jid(t["id"],role))
            if state in counts:counts[state]+=1
    return {"ok":True,"campaign":"chain2","roles":list(ROLES),
        "features":len(tickets),"stage_jobs":2*len(tickets),
        "root_lanes":cfg["seed_count"],"states":counts,
        "armed":armed(digest,len(tickets)),"held":HOLD.exists(),
        "ready":len(list(ready(cfg,tickets,digest)))}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["preview","request","arm"])
    p.add_argument("--parent")
    p.add_argument("--confirm-owner-arm",action="store_true")
    a=p.parse_args()
    try:
        if a.action=="preview":result=preview()
        elif a.action=="arm":result=arm(a.confirm_owner_arm)
        else:result=request(a.parent)
    except (OSError,ValueError,TypeError,KeyError,ChainError) as e:
        result={"ok":False,"error":str(e)}
    print(json.dumps(result,ensure_ascii=False))
    if not result.get("ok"):raise SystemExit(2)
if __name__=="__main__":main()
