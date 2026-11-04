"""One-time authorized independent QA handoff. No retries or descendants."""
import json,uuid
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"bridge-state"
OUT=STATE/"queue"
RUN="completion-gated-spec-qa-20261103"
JID=str(uuid.uuid5(uuid.NAMESPACE_URL,RUN))
PROMPT=("Ты независимый QA-архитектор Completion Gated Runtime. Через Local Workspace MCP "
        "вызови list_workspaces alias projects, прочитай "
        "chat-launch-smoke-desktop-test/agent-briefs/completion-integration-qa.md и "
        "completion-gated-integration.md, исполни весь бриф, создай изолированный "
        "тестовый прототип и сохрани отчёт completion-integration-qa.md. "
        "Не меняй основной код, разрешения, live SQLite и не создавай других агентов.")

def main():
    receipt=STATE/"done"/(JID+".json")
    active=STATE/"active"/(JID+".json")
    queued=OUT/(JID+".json")
    if receipt.exists() or active.exists() or queued.exists():
        return {"ok":True,"created":False,"existing":True,"id":JID}
    heartbeat=STATE/"diagnostics"/"extension.json"
    info=json.loads(heartbeat.read_text(encoding="utf-8"))
    if info.get("version")!="0.4.1":
        raise RuntimeError("version mismatch: extension must be 0.4.1")
    if (datetime.now(timezone.utc)-datetime.fromisoformat(info["at"])).total_seconds()>120:
        raise RuntimeError("heartbeat stale")
    if len(list(OUT.glob("*.json")))>=2 or any((STATE/"active").glob("*.json")):
        raise RuntimeError("queue not safely idle")
    OUT.mkdir(parents=True,exist_ok=True)
    obj={"id":JID,"prompt":PROMPT,"created":datetime.now(timezone.utc).isoformat(),
         "minHoldSeconds":180,"max_descendants":0,
         "source":RUN}
    with queued.open("x",encoding="utf-8") as f:
        json.dump(obj,f,ensure_ascii=False)
    return {"ok":True,"created":True,"id":JID,"minHoldSeconds":180}

if __name__=="__main__":
    print(json.dumps(main(),ensure_ascii=False))
