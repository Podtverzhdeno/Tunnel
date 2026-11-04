"""Bounded role-aware backlog: feature -> developer -> independent reviewer.

Inert until owner-armed. No external commands, browser actions, descendants,
automatic retries or unbounded generation. Bridge calls refill under its RLock.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from enqueue_completion import enqueue

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
STATE = ROOT / "bridge-state"
QUEUE, ACTIVE, DONE = (STATE / x for x in ("queue", "active", "done"))
ACKS, REPORTS = STATE / "completion-acks", PROJECT / "agent-reports"
BRIEFS = PROJECT / "agent-briefs"
HOLD, ARM = STATE / "admission-hold.json", STATE / "role-campaign-armed.json"
CAMPAIGNS = ROOT / "role-campaigns"
PHASES = ("feature", "developer", "reviewer")
PREVIOUS = {"feature": None, "developer": "feature", "reviewer": "developer"}
NAMESPACE = uuid.UUID("ad70a4f6-9183-48d4-99b5-b5db02b2bfea")
SLUG = re.compile(r"[a-z][a-z0-9-]{1,23}\Z")
ROLE = re.compile(r"[a-z][a-z0-9-]{1,47}\Z")

class CampaignError(RuntimeError):
    pass

def role_name(cid, ticket, stage):
    return f"rw-{cid}-{ticket}-{stage}"

def stage_id(cid, ticket, stage):
    return str(uuid.uuid5(NAMESPACE, f"{cid}/{ticket}/{stage}"))

def good_text(x, cap=700):
    return type(x) is str and 5 <= len(x.strip()) <= cap and "\x00" not in x

def load(campaign_id="eng1"):
    if not SLUG.fullmatch(campaign_id):
        raise CampaignError("invalid_campaign_id")
    path = CAMPAIGNS / (campaign_id + ".json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 120000:
        raise CampaignError("unsafe_or_missing_manifest")
    raw = path.read_bytes()
    d = json.loads(raw)
    keys = {"schema_version","campaign_id","name","allowed_project",
            "max_queued","max_active","requires_owner_arm","stages","tickets"}
    if type(d) is not dict or set(d) != keys:
        raise CampaignError("invalid_manifest_keys")
    if (d["schema_version"] != 1 or d["campaign_id"] != campaign_id
        or d["allowed_project"] != "chat-launch-smoke-desktop-test"
        or d["requires_owner_arm"] is not True or d["stages"] != list(PHASES)
        or type(d["max_queued"]) is not int or not 1 <= d["max_queued"] <= 4
        or type(d["max_active"]) is not int or not 1 <= d["max_active"] <= 25
        or not good_text(d["name"], 200)):
        raise CampaignError("unsafe_campaign_settings")
    if type(d["tickets"]) is not list or not 1 <= len(d["tickets"]) <= 50:
        raise CampaignError("invalid_ticket_count")
    seen = set()
    for t in d["tickets"]:
        if type(t) is not dict or set(t) != {"id","title","goal","acceptance",
                "failure_scenarios","interfaces","validation","priority"}:
            raise CampaignError("invalid_ticket_keys")
        key = t["id"]
        if (type(key) is not str or not SLUG.fullmatch(key) or key in seen
            or any(not ROLE.fullmatch(role_name(campaign_id,key,s)) for s in PHASES)):
            raise CampaignError("duplicate_or_invalid_role")
        seen.add(key)
        if (not good_text(t["title"],200) or not good_text(t["goal"],700)
            or type(t["priority"]) is not int or not 1 <= t["priority"] <= 10):
            raise CampaignError("invalid_ticket")
        for field, minimum, maximum in (("acceptance",5,12),
                ("failure_scenarios",3,10),("interfaces",2,8),("validation",3,8)):
            values = t[field]
            if (type(values) is not list or not minimum <= len(values) <= maximum
                or any(not good_text(x, 600) for x in values)):
                raise CampaignError("workload_too_small_or_invalid")
    return d, hashlib.sha256(raw).hexdigest()

def lookup(jid):
    files = [(stage,folder/(jid+".json")) for stage,folder in
             (("queue",QUEUE),("active",ACTIVE),("done",DONE))
             if (folder/(jid+".json")).is_file()]
    if len(files)>1:
        raise CampaignError("job_in_multiple_states")
    if not files:
        return None, None
    state, path = files[0]
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("id") != jid:
        raise CampaignError("job_identity_mismatch")
    return state, obj

def accepted(cid, ticket, stage, digest):
    jid = stage_id(cid,ticket,stage)
    state, data = lookup(jid)
    if state != "done" or data.get("status") != "done":
        return False
    report,receipt = REPORTS/("completion-"+jid+".md"), ACKS/(jid+".json")
    if (not report.is_file() or report.is_symlink()
        or not receipt.is_file() or receipt.is_symlink()):
        return False
    try:
        ack = json.loads(receipt.read_text(encoding="utf-8"))
        sha = hashlib.sha256(report.read_bytes()).hexdigest()
    except (OSError,ValueError):
        return False
    meta = data.get("role_metadata") or {}
    return (ack.get("status") == "REPORT_ACKED" and ack.get("job_id") == jid
        and sha == ack.get("artifact_sha256") == data.get("report_sha256")
        and ack.get("ack_id") == data.get("report_ack_id")
        and meta == {"campaign_id":cid,"ticket_id":ticket,
                     "stage":stage,"manifest_sha256":digest})

def ready(d, digest):
    cid = d["campaign_id"]
    tickets = sorted(d["tickets"],key=lambda x:(-x["priority"],x["id"]))
    for stage in ("reviewer","developer","feature"):
        for ticket in tickets:
            jid = stage_id(cid,ticket["id"],stage)
            state, _ = lookup(jid)
            if state is not None:
                continue  # No implicit replay, including failed/unknown.
            previous = PREVIOUS[stage]
            if previous is None or accepted(cid,ticket["id"],previous,digest):
                yield ticket, stage, jid

def bullets(items):
    return "\n".join("- "+item for item in items)

def brief_text(d,ticket,stage,jid):
    cid,tid = d["campaign_id"],ticket["id"]
    folder = f"chat-launch-smoke-desktop-test/experiments/role-workstreams/{cid}/{tid}/"
    role_title = {"feature":"Универсальный инженер-фичевладелец",
        "developer":"Универсальный разработчик кода",
        "reviewer":"Независимый универсальный ревьюер"}[stage]
    handoff = {
      "feature":"Разработай архитектуру, контракт, функциональный эталонный прототип, тесты и Handoff.",
      "developer":"Изучи feature/ только на чтение, реализуй полный работающий вариант, регрессию, API, CLI и документацию.",
      "reviewer":"Изучи feature/ и developer/ только на чтение. Независимо запусти тесты developer, напиши отрицательные сценарии, баг-репродукции и вердикт ACCEPT/REWORK/BLOCKED."
    }[stage]
    return f"""# {role_title}: {ticket['title']}
Job ID: {jid}
Campaign: {cid}
Ticket: {tid}
Role: {stage}

## Цель
{ticket['goal']}

## Задание крупного масштаба
{handoff}
Выполни несколько содержательных design/implement/test/fix циклов и все
критерии ниже. Сложность определяется инженерным результатом, а не временем.
Не используй пустые коммиты, sleep, искусственные тесты или генерацию файлов
ради создания видимости нагрузки.

## Acceptance criteria
{bullets(ticket['acceptance'])}

## Failure modes
{bullets(ticket['failure_scenarios'])}

## Interfaces
{bullets(ticket['interfaces'])}

## Verification evidence
{bullets(ticket['validation'])}

## Изоляция
Единственная директория, которую можно менять (кроме уникального отчёта):
{folder}{stage}/
При необходимости создай её. Читать предыдущие стадии можно, менять — нельзя.
Не изменяй live bridge, SQLite, расширение, настройки аккаунта, права, Git,
другие рабочие деревья. Нельзя создавать новых агентов или отправлять запросы
в браузер самостоятельно. Нельзя устанавливать зависимости. Для реализации
используй Python stdlib, содержательные автоматические тесты и README.
Если работа заблокирована, честно укажи blocker и сделанную часть.

## Completion
Запусти реальные тесты через Local Workspace, проверь job_status completed,
returncode=0 и job_output. Получи file_info с SHA результата.
Ревьюер дополнительно обязан проверить тесты исходного разработчика и
привести воспроизводимые основания замечаний. Вердикт reviewer не является
формальным одобрением для production. После работы напиши ровно один отчёт
в путь, указанный в launcher, c Job ID: {jid}, ## Outcome, ## Evidence,
## Tests, ## Risks, ## Handoff. Нельзя заявлять успешность без реального запуска.
"""

def ensure_brief(d,ticket,stage,jid):
    name = role_name(d["campaign_id"],ticket["id"],stage)
    path = BRIEFS / (name+".md")
    body = brief_text(d,ticket,stage,jid)
    if len(body.encode("utf-8")) > 32768:
        raise CampaignError("brief_too_large")
    BRIEFS.mkdir(parents=True,exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_text(encoding="utf-8") != body:
            raise CampaignError("immutable_brief_conflict")
    else:
        try:
            with path.open("x",encoding="utf-8",newline="\n") as out:
                out.write(body)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != body:
                raise CampaignError("immutable_brief_conflict")
    return "agent-briefs/"+path.name

def client_ready():
    try:
        ext = json.loads((STATE/"diagnostics"/"extension.json").read_text(encoding="utf-8"))
        seconds = (datetime.now(timezone.utc)-datetime.fromisoformat(ext["at"])).total_seconds()
        return (ext.get("version")=="0.6.6" and ext.get("state")=="polling"
                and type(ext.get("windows")) is int and ext["windows"]>=1
                and 0 <= seconds <= 120)
    except (ValueError,OSError,KeyError,TypeError):
        return False

def is_armed(d,digest):
    try:
        obj=json.loads(ARM.read_text(encoding="utf-8"))
        return (obj.get("enabled") is True and obj.get("campaign_id")==d["campaign_id"]
            and obj.get("manifest_sha256")==digest
            and obj.get("max_total_stages")==len(d["tickets"])*3)
    except (OSError,ValueError,TypeError):
        return False

def arm(cid="eng1",confirm=False):
    d,digest=load(cid)
    if not confirm:
        raise CampaignError("explicit_owner_arm_required")
    if HOLD.exists():
        raise CampaignError("admission_held")
    if any(QUEUE.glob("*.json")) or any(ACTIVE.glob("*.json")):
        raise CampaignError("existing_jobs_must_be_reconciled")
    if not client_ready():
        raise CampaignError("browser_not_ready")
    if ARM.exists():
        if is_armed(d,digest):
            return {"ok":True,"already_armed":True}
        raise CampaignError("conflicting_arm_record")
    marker={"enabled":True,"campaign_id":cid,"manifest_sha256":digest,
            "max_total_stages":len(d["tickets"])*3,
            "armed_at":datetime.now(timezone.utc).isoformat()}
    with ARM.open("x",encoding="utf-8") as f:
        json.dump(marker,f)
    return {"ok":True,**marker}

def claim_allowed(job):
    """Revalidate owner arm, role provenance and immutable brief at claim time."""
    meta=job.get("role_metadata")
    if meta is None:
        return True  # Existing legacy jobs preserve their original semantics.
    if type(meta) is not dict or set(meta)!={"campaign_id","ticket_id","stage","manifest_sha256"}:
        return False
    cid,tid,stage=meta["campaign_id"],meta["ticket_id"],meta["stage"]
    if not all(type(x) is str for x in meta.values()) or stage not in PHASES:
        return False
    try:
        d,digest=load(cid)
    except (OSError,ValueError,CampaignError):
        return False
    if (meta["manifest_sha256"]!=digest or not is_armed(d,digest)
        or HOLD.exists() or not any(t["id"]==tid for t in d["tickets"])
        or job.get("id")!=stage_id(cid,tid,stage)
        or job.get("role")!=role_name(cid,tid,stage)
        or job.get("completion_mode")!="report"
        or job.get("report_name")!="completion-"+job["id"]+".md"):
        return False
    brief=BRIEFS/(job["role"]+".md")
    if brief.is_symlink() or not brief.is_file():
        return False
    try:
        if job.get("brief_sha256")!=hashlib.sha256(brief.read_bytes()).hexdigest():
            return False
        ticket=next(t for t in d["tickets"] if t["id"]==tid)
        return PREVIOUS[stage] is None or accepted(cid,tid,PREVIOUS[stage],digest)
    except (OSError,ValueError):
        return False

def refill_unlocked(cid="eng1"):
    """Caller MUST hold bridge.LOCK for concurrent bridge GET requests."""
    d,digest=load(cid)
    if HOLD.exists():return {"ok":True,"queued":0,"reason":"admission_held"}
    if not is_armed(d,digest):return {"ok":True,"queued":0,"reason":"not_armed"}
    if not client_ready():return {"ok":True,"queued":0,"reason":"browser_not_ready"}
    active=list(ACTIVE.glob("*.json"))
    queued=list(QUEUE.glob("*.json"))
    # Do not mix campaign tasks with unrelated active/queued work.
    for path in active+queued:
        obj=json.loads(path.read_text(encoding="utf-8"))
        if (obj.get("role_metadata") or {}).get("campaign_id") != cid:
            return {"ok":True,"queued":0,"reason":"unowned_jobs_present"}
    space=min(d["max_queued"]-len(queued),25-len(queued),
              d["max_active"]-len(active)-len(queued))
    if space<=0:
        return {"ok":True,"queued":0,"reason":"capacity"}
    created=[]
    for ticket,stage,jid in ready(d,digest):
        if len(created)>=space:break
        brief=ensure_brief(d,ticket,stage,jid)
        meta={"campaign_id":cid,"ticket_id":ticket["id"],
              "stage":stage,"manifest_sha256":digest}
        result=enqueue(role_name(cid,ticket["id"],stage),brief,job_id=jid,metadata=meta)
        if not result.get("already_exists"):
            created.append({"ticket":ticket["id"],"role":stage,"job_id":jid})
    return {"ok":True,"queued":len(created),"jobs":created,
            "reason":"ready" if created else "exhausted_or_blocked"}

def preview(cid="eng1"):
    d,digest=load(cid)
    states={"queue":0,"active":0,"done":0}
    for t in d["tickets"]:
        for s in PHASES:
            state,_=lookup(stage_id(cid,t["id"],s))
            if state in states:states[state]+=1
    options=[{"ticket":t["id"],"role":s,"job_id":j}
             for t,s,j in ready(d,digest)]
    return {"ok":True,"campaign":cid,"manifest_sha256":digest,
        "features":len(d["tickets"]),"total_stage_jobs":len(d["tickets"])*3,
        "states":states,"ready_count":len(options),"ready_preview":options[:8],
        "held":HOLD.exists(),"armed":is_armed(d,digest),
        "client_ready":client_ready()}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["preview","arm"])
    p.add_argument("--campaign",default="eng1")
    p.add_argument("--confirm-owner-arm",action="store_true")
    args=p.parse_args()
    try:
        if args.action=="preview":result=preview(args.campaign)
        else:result=arm(args.campaign,args.confirm_owner_arm)
    except (CampaignError,ValueError,RuntimeError,OSError) as exc:
        result={"ok":False,"error":str(exc)}
    print(json.dumps(result,ensure_ascii=False))
    if not result.get("ok"):raise SystemExit(2)

if __name__=="__main__":
    main()
