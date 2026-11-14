"""Real Edge/Playwright DOM acceptance for Hermes live CDP worker, offline fixture.

Uses isolated temporary Edge profile and ephemeral Relay HTTP/SQLite. The
chatgpt.com URL is fulfilled by Playwright route(), never fetched from network.
No live ChatGPT, Telegram credentials, extensions or existing browser profile.
"""
from __future__ import annotations
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from urllib.parse import quote
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
CHAT="https://chatgpt.com/c/1234abcd-1234-4abc-8abc-123456789abc"
CDP="http://127.0.0.1:9223"
ROOT_FIXTURE_HTML=r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Hermes isolated DOM</title>
<style>form,button,[contenteditable]{display:block;min-height:25px;margin:20px}</style>
</head><body>
<main id="messages">
<div data-message-author-role="user">old conversation user message</div>
<div data-message-author-role="assistant">old assistant answer</div>
</main>
<div id="challenge" role="dialog" hidden>Log in</div>
<form id="composer"><div id="prompt-textarea" data-testid="prompt-textarea"
contenteditable="true" role="textbox"></div>
<button id="composer-submit-button" data-testid="send-button" aria-label="Send" disabled>Send</button>
</form>
<script>
const CASE = "__CASE__";
const editor=document.getElementById("prompt-textarea");
const send=document.getElementById("composer-submit-button");
const messages=document.getElementById("messages");
const dialog=document.getElementById("challenge");
window.sendCount=0;
window.postedUser=0;
window.postedAssistant=0;
if(CASE==="challenge"){dialog.hidden=false;dialog.removeAttribute("hidden");}
editor.addEventListener("input",()=>{send.disabled=!editor.innerText.trim();});
send.addEventListener("click", ev=>{
    ev.preventDefault();
    window.sendCount++;
    const prompt=editor.innerText;
    const id=(prompt.match(/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/)||[])[0];
    if (CASE!=="no_user") {
        const user=document.createElement("div");
        user.setAttribute("data-message-author-role","user");
        user.innerText=CASE==="foreign_user"?"unrelated manual message":prompt;
        messages.appendChild(user);
        window.postedUser++;
    }
    const reply=document.createElement("div");
    reply.setAttribute("data-message-author-role","assistant");
    reply.innerText=JSON.stringify({request_id:id,content:"LIVE_DOM_FIXTURE_OK",tool_calls:[]});
    messages.appendChild(reply);
    window.postedAssistant++;
    editor.innerText="";
    send.disabled=true;
});
</script></body></html>"""

def isolated_case(mode, expected):
    import relay
    from hermes_web_live_cdp import execute_attached_once, LiveCDPFault
    from hermes_cdp_relay_client import ScopedWorkerHttp
    from cdp_send_journal import SendJournal
    from playwright.async_api import async_playwright
    from secrets import token_urlsafe

    async def run_case():
        with tempfile.TemporaryDirectory(prefix="hermes-live-dom-") as td:
            base=Path(td)
            queue=relay.Queue(base/"relay.sqlite3")
            key=token_urlsafe(40); worker_key=token_urlsafe(40)
            srv=relay.Server(("127.0.0.1",0),queue,key,timeout=14,
                             enable_playwright=True,playwright_key=worker_key)
            th=threading.Thread(target=srv.serve_forever,daemon=True)
            th.start()
            try:
                request={
                    "model":relay.MODEL_CDP,
                    "messages":[{"role":"user","content":"Return LIVE_DOM_FIXTURE_OK"}]
                }
                rid=queue.submit(request,transport="playwright")
                journal_dir=base/"journal";journal_dir.mkdir()
                journal=SendJournal(journal_dir)
                http=ScopedWorkerHttp(f"http://127.0.0.1:{srv.server_address[1]}",worker_key)
                async with async_playwright() as pw:
                    ctx=await pw.chromium.launch_persistent_context(
                        user_data_dir=str(base/"disposable-browser"),
                        channel="msedge",headless=True,viewport={"width":1100,"height":800})
                    try:
                        page=await ctx.new_page()
                        async def route_chat(route):
                            await route.fulfill(status=200,content_type="text/html",
                                                body=ROOT_FIXTURE_HTML.replace("__CASE__",mode))
                        await page.route(CHAT,route_chat)
                        await page.goto(CHAT,wait_until="domcontentloaded")
                        if mode=="ready":
                            # Test-only: locate the exact mismatch after real
                            # Playwright fills a multiline contenteditable.
                            source=relay.prompt_for(request,rid)
                            editor=page.locator("#prompt-textarea")
                            await editor.fill(source)
                            visible=await editor.inner_text()
                            actual=await editor.evaluate(r"""(root) => {
                                const BLOCK=new Set(['DIV','P']);
                                function extract(node) {
                                    if (node.nodeType===Node.TEXT_NODE) return node.nodeValue;
                                    if (node.nodeType!==Node.ELEMENT_NODE) return '';
                                    if (node.tagName==='BR') {
                                        const p=node.parentElement;
                                        return p && BLOCK.has(p.tagName)
                                          && p.childNodes.length===1 ? '' : '\n';
                                    }
                                    let out='';
                                    for (const [i,child] of [...node.childNodes].entries()) {
                                        if (i && child.nodeType===Node.ELEMENT_NODE
                                              && BLOCK.has(child.tagName)) out+='\n';
                                        out+=extract(child);
                                    }
                                    return out;
                                }
                                return extract(root);
                            }""")
                            if actual!=source:
                                mismatch=next((j for j,(a,b) in enumerate(zip(source,actual))
                                               if a!=b),min(len(source),len(actual)))
                                markup=await editor.inner_html()
                                raise AssertionError(
                                    f"markup_focus={repr(markup[1300:1560])} "
                                    f"expected_len={len(source)} text_len={len(actual)} "
                                    f"visible_len={len(visible)} index={mismatch}")
                            await editor.fill("")
                        async def owned_connector(_):
                            return SimpleNamespace(contexts=[ctx])
                        result=await execute_attached_once(
                            http,CDP,CHAT,journal,browser_connector=owned_connector,
                            timeout_s=1 if mode in ("no_user","foreign_user") else 5)
                        clicks=await page.evaluate("window.sendCount")
                        state=queue.read(rid)["state"]
                        if result["outcome"]!=expected:
                            raise AssertionError(f"outcome {result}, expected {expected}")
                        if mode=="challenge" and clicks!=0:
                            raise AssertionError("challenge_clicked_send")
                        if mode!="challenge" and clicks!=1:
                            raise AssertionError("send_not_exactly_once")
                        if expected=="uncertain":
                            assert state=="uncertain",state
                            assert (journal_dir/("chat-"+__import__("hashlib").sha256(CHAT.encode()).hexdigest()+".lease")).is_file()
                        else:
                            assert state==expected,state
                        return {"mode":mode,"outcome":expected,"state":state,
                                "browser_sends":clicks,"extension_used":False}
                    finally:
                        await ctx.close()
            finally:
                srv.shutdown();srv.server_close();th.join(timeout=3)
                queue.close()
    return asyncio.run(run_case())

def main():
    cases=(("ready","done"),("challenge","blocked"),
           ("no_user","uncertain"),("foreign_user","uncertain"))
    results=[isolated_case(*c) for c in cases]
    print(json.dumps({"ok":True,"cases":results,"browser":"real_Edge",
                      "playwright":"real", "relay_http":"real_ephemeral",
                      "chatgpt_http_requests":0,"telegram_messages_sent":0,
                      "live_profile_modified":False},sort_keys=True))
    return 0

if __name__=="__main__":
    venv=ROOT/".venv-cdp"/"Scripts"/"python.exe"
    if Path(sys.executable).resolve()!=venv.resolve():
        if not venv.is_file():
            print(json.dumps({"ok":False,"reason":"venv_cdp_missing"}))
            raise SystemExit(2)
        completed=subprocess.run([str(venv),str(Path(__file__).resolve())],cwd=ROOT,
              capture_output=True,text=True,encoding="utf8",errors="replace",timeout=115)
        print(completed.stdout.strip()[-6000:] or '{"ok":false,"reason":"no_child_receipt"}')
        if completed.returncode!=0:
            print(completed.stderr.strip()[-2500:],file=sys.stderr)
        raise SystemExit(completed.returncode)
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        t=traceback.extract_tb(exc.__traceback__)
        line=t[-1].lineno if t else None
        print(json.dumps({"ok":False,"error_class":type(exc).__name__,
                          "detail":str(exc)[:300],
                          "source":Path(t[-1].filename).name if t else None,
                          "line":line},sort_keys=True))
        raise SystemExit(1)
