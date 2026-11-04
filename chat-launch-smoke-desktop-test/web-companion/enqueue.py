"""Enqueue exactly one foreground-authorized browser task; no browser windows.
Script does not send messages by itself. Running it is a single explicit request.
"""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
QUEUE=ROOT/"bridge-state"/"queue"
ACTIVE=ROOT/"bridge-state"/"active"
DEFAULT="Тест автономного фонового чата. Ответь одним словом: ПРИНЯТО."

def enqueue(prompt):
    prompt=prompt.strip()
    if not 1 <= len(prompt) <= 1000: raise ValueError("prompt_length_out_of_range")
    QUEUE.mkdir(parents=True,exist_ok=True)
    ACTIVE.mkdir(parents=True,exist_ok=True)
    # Limit unattended requests: one active and at most 2 waiting.
    if len(list(QUEUE.glob("*.json"))) >= 2:
        raise ValueError("queue_full")
    jid=str(uuid.uuid4())
    data={"id":jid,"prompt":prompt,"created":datetime.now(timezone.utc).isoformat()}
    path=QUEUE/(jid+".json")
    with path.open("x",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False)
    return {"queued":True,"id":jid,"browser_window_opened":False,"background_extension_required":True}

if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--prompt",default=DEFAULT)
    args=a.parse_args()
    print(json.dumps(enqueue(args.prompt),ensure_ascii=False))
