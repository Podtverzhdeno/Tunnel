"""A standalone, one-job Playwright worker against a LOCAL FIXTURE ONLY.

Security boundaries:
- Deliberately **cannot open chatgpt.com** or use an authenticated profile;
- requires a localhost synthetic fixture /health response;
- accepts only a one-use temporary worker bearer from environment;
- launches its OWN temporary Edge profile, not any existing browser;
- claims exactly one Playwright-only job and NEVER retries Send;
- persists the Send journal before the click and emits a bounded receipt;
- never reads or prints messages, tokens, leases, or browser responses.

This is a staging worker process, not a production gateway or a production
fallback. It must not be installed as a service or called with a live Relay.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/"planning"))
sys.path.insert(0,str(HERE.parent/"send-journal-v1"))

from cdp_fixture_client import (
    FixtureBrowserError, inspect_and_run, verify_fixture_health, verify_local_url
)
from cdp_send_journal import SendJournal, ARMED, PREPARED, VERIFIED, AlreadyReserved
from playwright.async_api import async_playwright


class WorkerFault(RuntimeError):
    pass


CASES={
    "ready","challenge","duplicate_send","wrong_chat",
    "close_before_click","close_after_click","missing_composer"
}
TEST_GATE="LOCAL_FIXTURE_ONLY_NO_PRODUCTION"
MAX_HTTP_REPLY=600000


def only_loopback_relay(raw: str) -> str:
    # Strict URL parser with no userinfo, query, fragments or DNS resolution.
    try:
        u=urlsplit(raw)
        port=u.port
    except (TypeError,ValueError) as exc:
        raise WorkerFault("relay_url_invalid") from exc
    if (u.scheme!="http" or u.hostname!="127.0.0.1" or
        u.netloc!=f"127.0.0.1:{port}" or not port or
        u.path not in {"","/"} or u.query or u.fragment or u.username or u.password):
        raise WorkerFault("relay_loopback_only")
    return f"http://127.0.0.1:{port}"


class ScopedWorkerHttp:
    def __init__(self, relay: str, key: str):
        self.relay=only_loopback_relay(relay)
        if not isinstance(key,str) or len(key)<32 or len(key)>256:
            raise WorkerFault("ephemeral_worker_key_required")
        self.key=key
        self.opener=build_opener(ProxyHandler({}))

    def post(self, path: str, data: dict):
        if path not in {"/bridge/claim","/bridge/event"}:
            raise WorkerFault("worker_endpoint_not_allowed")
        request=Request(
            self.relay+path,method="POST",
            data=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8"),
            headers={"Authorization":"Bearer "+self.key,
                     "Content-Type":"application/json"})
        try:
            with self.opener.open(request,timeout=8) as response:
                status=response.status
                body=response.read(MAX_HTTP_REPLY+1)
        except HTTPError as exc:
            status=exc.code
            body=exc.read(MAX_HTTP_REPLY+1)
        if len(body)>MAX_HTTP_REPLY:
            raise WorkerFault("relay_reply_too_large")
        try:
            parsed=json.loads(body)
        except (ValueError,UnicodeError) as exc:
            raise WorkerFault("invalid_relay_reply") from exc
        if not isinstance(parsed,dict):
            raise WorkerFault("relay_reply_not_object")
        return status,parsed


def validate_job(job: dict):
    if not isinstance(job,dict):
        raise WorkerFault("claimed_job_invalid")
    if job.get("transport")!="playwright":
        raise WorkerFault("claim_transport_mismatch")
    rid=job.get("id")
    try:
        value=uuid.UUID(rid)
        if value.version!=4 or str(value)!=rid:
            raise ValueError("noncanonical_uuid4")
    except (ValueError,AttributeError,TypeError) as exc:
        raise WorkerFault("claim_request_identity_invalid") from exc
    if not isinstance(job.get("lease"),str) or len(job["lease"])<16:
        raise WorkerFault("lease_missing")
    if not isinstance(job.get("prompt"),str) or rid not in job["prompt"]:
        raise WorkerFault("bound_prompt_mismatch")
    return rid


async def get_one_job(http: ScopedWorkerHttp):
    worker=str(uuid.uuid4())
    deadline=time.monotonic()+9
    while time.monotonic()<deadline:
        status,payload=await asyncio.to_thread(
            http.post,"/bridge/claim",{"worker":worker,"transport":"playwright"})
        if status!=200 or payload.get("ok") is not True:
            raise WorkerFault("relay_claim_denied")
        if payload.get("job") is not None:
            validate_job(payload["job"])
            return payload["job"]
        await asyncio.sleep(.08)
    raise WorkerFault("fixture_job_not_available")


async def event(http, rid, lease, action, *, page_key=None, url=None, text=None, reason=None):
    body={"id":rid,"lease":lease,"action":action}
    if page_key is not None:body["page_key"]=page_key
    if url is not None:body["url"]=url
    if text is not None:body["text"]=text
    if reason is not None:body["reason"]=reason
    return await asyncio.to_thread(http.post,"/bridge/event",body)


async def execute_one(http: ScopedWorkerHttp, fixture_url: str, case: str,
                      journal: SendJournal):
    job=await get_one_job(http)
    rid=job["id"]
    lease=job["lease"]
    journal.reserve(rid)
    page_key=str(uuid.uuid4())
    relay_intent_submitted=False
    relay_intent_confirmed=False
    phase_after="prepared"
    browser_clicks=None
    last_relay_state="claimed"
    receipt_verified=False

    # Only new, owned ephemeral browser and page; never attach existing Chrome.
    with tempfile.TemporaryDirectory(prefix="hermes-worker-v2-edge-") as directory:
        async with async_playwright() as playwright:
            context=await playwright.chromium.launch_persistent_context(
                user_data_dir=directory,channel="msedge",
                headless=True,viewport={"width":1024,"height":768})
            try:
                page=await context.new_page()
                base_case="ready" if case.startswith("close_") else case
                await page.goto(fixture_url+"?case="+base_case,
                                wait_until="domcontentloaded",timeout=15000)
                verify_local_url(fixture_url)
                verify_local_url(page.url.replace("?case="+base_case,""))
                root=page.locator("#fixture")
                if (await root.count()!=1 or
                        await root.get_attribute("data-fixture-only")!="true"):
                    raise WorkerFault("local_fixture_not_attested")

                try:
                    await inspect_and_run(page,request_id=rid,allow_fixture_send=False,
                                          timeout_ms=1800)
                except FixtureBrowserError as preflight:
                    journal.block_pre_send(rid)
                    status,ack=await event(http,rid,lease,"blocked",
                                           reason=preflight.code.lower())
                    if status!=200 or ack.get("state")!="blocked":
                        raise WorkerFault("preflight_block_ack_unknown")
                    return {"outcome":"blocked","journal_phase":"blocked_pre_send",
                            "relay_state":"blocked","send_count":0}

                # Relay and disk journal are BOTH fenced before a browser click.
                # A lost intent ACK is not a click authorization.
                relay_intent_submitted=True
                status,ack=await event(http,rid,lease,"intent",page_key=page_key)
                if status!=200 or ack.get("state")!="intent":
                    raise WorkerFault("relay_intent_not_confirmed")
                relay_intent_confirmed=True
                journal.arm_before_click(rid)
                phase_after=ARMED

                if case=="close_before_click":
                    # Proven no browser click has been issued in this branch.
                    browser_clicks=0
                    await page.close()
                    raise WorkerFault("owned_page_closed_before_click")

                if case=="close_after_click":
                    # Test-only fault injection: exactly one synthetic click
                    # followed by immediate page closure and no assistant ACK.
                    editor=page.locator("#composer")
                    send=page.locator("#send")
                    if (await editor.count()!=1 or await send.count()!=1 or
                            not await editor.is_editable() or not await send.is_enabled()):
                        raise WorkerFault("fixture_editor_changed")
                    await editor.fill(json.dumps({
                        "request_id":rid,
                        "request":{"messages":[{"role":"user",
                                                "content":"LOCAL_FIXTURE_ONLY"}]}}))
                    await send.click(timeout=2500)
                    browser_clicks=int(await root.get_attribute("data-send-count") or "0")
                    await page.close()
                    raise WorkerFault("owned_page_closed_after_click")

                browser_reply=await inspect_and_run(
                    page,request_id=rid,allow_fixture_send=True,timeout_ms=2800)
                if browser_reply.get("receipt")!="LOCAL_FIXTURE_OK":
                    raise WorkerFault("fixture_receipt_mismatch")
                turns=page.locator('[data-testid="assistant-turn"]')
                if await turns.count()!=1:
                    raise WorkerFault("assistant_identity_ambiguous")
                raw=await turns.inner_text(timeout=2000)
                try:
                    exact=json.loads(raw)
                except ValueError as exc:
                    raise WorkerFault("assistant_json_invalid") from exc
                if exact!={"request_id":rid,"content":"LOCAL_FIXTURE_OK","tool_calls":[]}:
                    raise WorkerFault("assistant_exact_identity_not_proven")
                receipt_verified=True
                journal.complete_fixture_response(
                    rid,exact_identity_proven=True)
                phase_after=VERIFIED

                # These are result ACK events, not another Send. A lost ACK
                # does not authorize replaying browser content or the click.
                for action in ("saved","result"):
                    payload={"page_key":page_key,"url":fixture_url}
                    if action=="result":payload["text"]=raw
                    status,ack=await event(http,rid,lease,action,**payload)
                    if status!=200 or ack.get("state")!=(
                        "saved" if action=="saved" else "done"):
                        raise WorkerFault("relay_result_ack_unknown")
                    last_relay_state=ack["state"]

                browser_clicks=int(
                    await root.get_attribute("data-send-count") or "0")
                if browser_clicks!=1:
                    raise WorkerFault("fixture_send_count_invalid")
                return {"outcome":"done","journal_phase":VERIFIED,
                        "relay_state":"done","send_count":browser_clicks}
            except BaseException as exc:
                # Read a bounded, fixture-only click counter for diagnostics.
                # This must never trigger another UI effect. Closed pages
                # remain unobserved rather than being reopened or replayed.
                if browser_clicks is None:
                    try:
                        if not page.is_closed():
                            browser_clicks = int(
                                await root.get_attribute("data-send-count",timeout=700) or "0")
                    except Exception:
                        pass
                # After Relay intent, ANY uncertain outcome is noReplay.
                # Never send another click or return request data to the log.
                current=journal.read(rid)
                state=current["phase"]
                if state==ARMED:
                    reason="page_closed" if case.startswith("close_") else (
                        "click_or_response_unconfirmed")
                    journal.mark_unknown(rid,reason)
                    phase_after="send_outcome_unknown"
                elif state==PREPARED:
                    journal.block_pre_send(rid)
                    phase_after="blocked_pre_send"
                else:
                    phase_after=state
                if relay_intent_submitted:
                    # For a committed intent -> uncertain. If claim was still
                    # pre-intent -> blocked. Best-effort only, no result retry.
                    try:
                        status,ack=await event(
                            http,rid,lease,"blocked",page_key=page_key,
                            reason="web_chat_changed_after_send")
                        if status==200:
                            last_relay_state=ack.get("state","unverified")
                    except Exception:
                        last_relay_state="ack_unknown"
                else:
                    try:
                        status,ack=await event(
                            http,rid,lease,"blocked",
                            reason="web_fixture_not_ready")
                        if status==200:last_relay_state=ack.get("state","unverified")
                    except Exception:last_relay_state="ack_unknown"
                return {
                    "outcome":"uncertain" if relay_intent_submitted else "blocked",
                    "journal_phase":phase_after,
                    "relay_state":last_relay_state,
                    "send_count":browser_clicks,
                    "fixture_response_seen":receipt_verified,
                    "fault_class":type(exc).__name__,
                }
            finally:
                await context.close()


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--relay",required=True)
    parser.add_argument("--fixture",required=True)
    parser.add_argument("--journal",required=True)
    parser.add_argument("--case",required=True,choices=sorted(CASES))
    parser.add_argument("--fixture-only",required=True)
    args=parser.parse_args()
    if (args.fixture_only!=TEST_GATE or
            os.environ.get("HERMES_CDP_FIXTURE_ONLY")!="1"):
        raise WorkerFault("explicit_local_fixture_gate_required")
    relay=only_loopback_relay(args.relay)
    verify_local_url(args.fixture)
    verify_fixture_health(args.fixture)
    if not Path(args.journal).is_absolute():
        raise WorkerFault("absolute_journal_directory_required")
    journal=SendJournal(args.journal)
    key=os.environ.get("HERMES_CDP_FIXTURE_WORKER_KEY","")
    http=ScopedWorkerHttp(relay,key)
    return await execute_one(http,args.fixture,args.case,journal)


if __name__=="__main__":
    try:
        result=asyncio.run(main())
        print(json.dumps({"ok":result["outcome"] in {"done","blocked","uncertain"},
                          "transport":"playwright",
                          "local_fixture":True,"profile":"disposable",
                          "production_activated":False,
                          "real_chatgpt_sends":0,"telegram_sends":0,
                          **result},sort_keys=True),flush=True)
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,
                          "reason":"fixture_worker_failed_closed",
                          "real_chatgpt_sends":0,"telegram_sends":0},sort_keys=True),
              flush=True)
        sys.exit(2)
