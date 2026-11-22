"""Installed Hermes Telegram SDK -> ephemeral Relay, mock Telegram Bot API.

Runs only inside an installed Hermes CLI Python environment with a temporary
HOME. The inbound MessageEvent is synthetic; no actual Telegram call exists.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, SendResult
from gateway.session import SessionSource
from hermes_telegram_lab import LabTelegram, Trace
import relay


class Sink(LabTelegram):
    def __init__(self):
        super().__init__(Trace("ok"))
        self.delivered=[]

    async def send(self,chat_id,content,reply_to=None,metadata=None):
        self.delivered.append({
            "chat_id":str(chat_id),
            "content_matches":content=="LIVE_DOM_FIXTURE_OK",
            "reply_to":str(reply_to) if reply_to is not None else None,
            "metadata":metadata,
        })
        return SendResult(success=True,message_id="synthetic-send-ack-1")


async def run():
    base=os.environ.get("OPENAI_BASE_URL","")
    key=os.environ.get("OPENAI_API_KEY","")
    if not base.startswith("http://127.0.0.1:") or not base.endswith("/v1"):
        raise RuntimeError("only_ephemeral_loopback_model_provider")
    if len(key)<32:
        raise RuntimeError("ephemeral_client_key_missing")
    adapter=Sink()
    chat="-900"+str(uuid.uuid4().int % 100000000)
    thread_id="14"  # synthetic Telegram supergroup forum topic
    marker="HERMES_MCP_TG_FIXTURE_"+uuid.uuid4().hex

    async def handler(event):
        request={
            "model":relay.MODEL_CDP,
            "stream":False,
            "messages":[{"role":"user","content":marker}],
        }
        def post():
            data=json.dumps(request,separators=(",",":")).encode("utf8")
            req=urllib.request.Request(
                base+"/chat/completions",data=data,method="POST",
                headers={"Content-Type":"application/json",
                         "Authorization":"Bearer "+key})
            with urllib.request.urlopen(req,timeout=75) as response:
                if response.status!=200:
                    raise RuntimeError("ephemeral_relay_http_failure")
                return json.loads(response.read(120000))
        answer=await asyncio.to_thread(post)
        return answer["choices"][0]["message"]["content"]

    adapter.set_message_handler(handler)
    event=MessageEvent(
        text=marker,
        source=SessionSource(platform=Platform.TELEGRAM,
               chat_id=chat,user_id="synthetic-mcp-telegram",
               chat_type="supergroup",thread_id=thread_id),
        message_id=str(uuid.uuid4().int % 800000000+100000000)
    )
    await adapter.handle_message(event)
    tasks=list(adapter._background_tasks)
    if not tasks:
        raise AssertionError("native_sdk_background_handler_not_scheduled")
    await asyncio.wait_for(asyncio.gather(*tasks),timeout=85)
    topic_ok=(len(adapter.delivered)==1 and
        isinstance(adapter.delivered[0]["metadata"],dict) and
        str(adapter.delivered[0]["metadata"].get("thread_id"))==thread_id)
    ok=(len(adapter.delivered)==1 and
        adapter.delivered[0]["chat_id"]==chat and
        adapter.delivered[0]["content_matches"] is True and topic_ok)
    report={
        "ok":ok,
        "native_telegram_sdk_inbound":True,
        "mock_telegram_send_result_ack":len(adapter.delivered)==1,
        "mock_reply_same_chat":ok,
        "mock_reply_same_topic":topic_ok,
        "mock_outbound_count":len(adapter.delivered),
        "source_thread_bound":event.source.thread_id==thread_id,
        "mock_outbound_metadata":adapter.delivered[0]["metadata"] if adapter.delivered else None,
        "model":relay.MODEL_CDP,
        "telegram_bot_api_requests":0,
        "chatgpt_web_requests":0
    }
    print(json.dumps(report,sort_keys=True),flush=True)
    if not ok:
        raise AssertionError("synthetic_telegram_model_delivery_failed")


if __name__=="__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,
                          "phase":"synthetic_telegram_sdk"},sort_keys=True))
        raise SystemExit(1)
