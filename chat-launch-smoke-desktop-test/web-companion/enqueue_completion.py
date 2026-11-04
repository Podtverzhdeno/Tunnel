"""Queue one explicitly requested completion-gated research chat (no descendants).

Does not claim official OpenSpec/SDD acceptance or a trusted Windows actor.
Its durable browser-closure ACK verifies a scoped output's integrity only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
QUEUE = ROOT / "bridge-state" / "queue"
ACTIVE = ROOT / "bridge-state" / "active"
DONE = ROOT / "bridge-state" / "done"
VALID_ROLE = re.compile(r"[a-z][a-z0-9-]{1,47}\Z")

def enqueue(role: str, brief: str, *, job_id: str | None = None,
            metadata: dict | None = None) -> dict:
    if not VALID_ROLE.fullmatch(role):
        raise ValueError("invalid_role")
    brief_path = PROJECT / "agent-briefs" / (role+".md")
    if brief != str(brief_path.relative_to(PROJECT)).replace("\\", "/"):
        raise ValueError("brief_must_match_role_and_allowlist")
    if not brief_path.is_file() or brief_path.is_symlink():
        raise ValueError("brief_missing_or_link")
    if brief_path.stat().st_size > 32 * 1024:
        raise ValueError("brief_too_large")
    content_sha=hashlib.sha256(brief_path.read_bytes()).hexdigest()
    if (job_id is None) != (metadata is None):
        raise ValueError("role_id_and_metadata_must_be_paired")
    if metadata is not None:
        if type(metadata) is not dict or set(metadata)!={"campaign_id","ticket_id","stage","manifest_sha256"}:
            raise ValueError("invalid_role_metadata")
        if any(type(v) is not str or not v or len(v)>64 for v in metadata.values()):
            raise ValueError("invalid_role_metadata")
    if job_id is not None:
        if type(job_id) is not str:
            raise ValueError("invalid_job_id")
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError("invalid_job_id")
        except (ValueError,AttributeError):
            raise ValueError("invalid_job_id") from None
    jid = job_id or str(uuid.uuid4())
    if job_id is not None:
        # Crash/restart-safe: a deterministic campaign stage is sent at most once.
        # Existing queue/active/done records are never replayed or rewritten.
        for root in (QUEUE,ACTIVE,DONE):
            prior=root/(jid+".json")
            if prior.exists():
                old=json.loads(prior.read_text(encoding="utf-8"))
                if (old.get("id")!=jid or old.get("role")!=role
                    or old.get("brief_sha256")!=content_sha
                    or old.get("role_metadata")!=metadata):
                    raise RuntimeError("deterministic_job_conflict")
                return {"queued":root==QUEUE,"job_id":jid,
                        "report":"agent-reports/completion-"+jid+".md",
                        "completion_mode":"report","already_exists":True}
    QUEUE.mkdir(parents=True, exist_ok=True)
    ACTIVE.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    # Up to 25 pending jobs; the independent bridge enforces 25 active tabs.
    if len(list(QUEUE.glob("*.json"))) >= 25:
        raise ValueError("queue_full")
    heartbeat=ROOT/"bridge-state"/"diagnostics"/"extension.json"
    if not heartbeat.is_file():
        raise RuntimeError("extension_heartbeat_missing")
    state=json.loads(heartbeat.read_text(encoding="utf-8"))
    if state.get("version")!="0.6.6":
        raise RuntimeError("reload_extension_v0.6.6_before_dispatch")
    if state.get("state")!="polling":
        raise RuntimeError("extension_not_ready")
    last=datetime.fromisoformat(state["at"])
    if (datetime.now(timezone.utc)-last).total_seconds()>120:
        raise RuntimeError("extension_heartbeat_stale")
    report_name = "completion-"+jid+".md"
    is_chain=bool(metadata and metadata.get("campaign_id") in ("chain2","chain3"))
    ending=(
      "Не меняй основной код или live SQLite; в конце вызови через MCP "
      "один идемпотентный запрос на преемника по инструкции брифа, затем запиши отчёт. "
      if is_chain else
      "Не меняй основной код, live SQLite, не запускай других агентов. "
    )
    message = (
      "Ты независимый исследователь Completion-Gated Runtime. "
      "Подключи Local Workspace MCP: list_workspaces alias projects. "
      "Прочитай полностью chat-launch-smoke-desktop-test/"+brief+
      " через MCP. Если read_file возвращает пустой объект {}, используй "
      "Local Workspace run_script: workspace=projects, "
      "path=Local-Workspace-Extensions/ops.py, cwd=Local-Workspace-Extensions, "
      "args=[read_text, chat-launch-smoke-desktop-test/"+brief+
      ", --start-line, 1, --max-lines, 200]. "
      "Обязательно проверь job_status (completed, returncode=0) и job_output "
      "(ok=true); если бриф длиннее 200 строк, прочитай следующие фрагменты. "
      "После полного чтения выполни задание и проверь фактические результаты. "
      "Запиши ровно один новый отчёт через Local Workspace write_file "
      "в chat-launch-smoke-desktop-test/agent-reports/"+report_name+". "
      "В отчёте обязательно отдельными строками: Job ID: "+jid+
      ", затем заголовки ## Outcome и ## Evidence с содержательными выводами "
      "и проверенными тестами. Без этого отчёт не будет принят системой и "
      "браузерная вкладка останется открытой. "+ending+
      "Нельзя объявлять тесты успешными без запуска."
    )
    if len(message) > 6000:
        raise ValueError("prompt_too_long")
    obj = {
      "id": jid, "prompt":message, "completion_mode":"report",
      "report_name":report_name, "minHoldSeconds":180,
      "descendant_budget":0, "role":role, "created":datetime.now(timezone.utc).isoformat()
    }
    if job_id is not None:
        obj["brief_sha256"]=content_sha
        obj["role_metadata"]=metadata
    with (QUEUE/(jid+".json")).open("x",encoding="utf-8") as f:
        json.dump(obj,f,ensure_ascii=False)
        f.flush()
    return {"queued":True,"job_id":jid,"report":"agent-reports/"+report_name,
            "completion_mode":"report","trust_level":"advisory_integrity_only"}
if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--role",required=True)
    parser.add_argument("--brief",required=True)
    args=parser.parse_args()
    print(json.dumps(enqueue(args.role,args.brief),ensure_ascii=False))
