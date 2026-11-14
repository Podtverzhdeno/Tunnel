"""Hermes CLI -> existing Relay model route -> MCP sidecar -> REAL Edge CDP -> CLI.

Everything is ephemeral: installed CLI is called with a temporary HERMES_HOME,
temporary Relay HTTP/SQLite, disposable Edge profile, and a Playwright-fulfilled
synthetic ChatGPT-like DOM. Nothing is sent to ChatGPT Web or Telegram.
"""
from __future__ import annotations
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
if str(ROOT/"planning") not in sys.path:
    sys.path.insert(0,str(ROOT/"planning"))

from hermes_live_dom_fixture_e2e import CHAT, ROOT_FIXTURE_HTML
import relay


async def run(client_kind='cli', reply_mode='ready'):
    # Imports requiring Playwright/MCP SDK happen only after the isolated
    # virtualenv trampoline; the system Python has neither package.
    from cdp_attach_smoke_suite import edge_path, debug_port
    from hermes_cli_mcp_cdp_fixture_e2e import clean_env
    from playwright.async_api import async_playwright
    local=os.environ.get("LOCALAPPDATA")
    if not local:
        raise AssertionError("installed_hermes_location_unavailable")
    cli=Path(local)/"hermes"/"hermes-agent"/"venv"/"Scripts"/"python.exe"
    if not cli.is_file():
        raise AssertionError("installed_hermes_python_unavailable")
    begin=time.monotonic()
    with ExitStack() as guard:
        base=Path(guard.enter_context(tempfile.TemporaryDirectory(prefix="hermes-mcp-model-cycle-")))
        hermes_home=base/"hermes";hermes_home.mkdir()
        journal=base/"journal";journal.mkdir()
        browser_profile=base/"edge";browser_profile.mkdir()
        queue=relay.Queue(base/"relay.sqlite3")
        guard.callback(queue.close)
        client_key=secrets.token_urlsafe(40)
        worker_key=secrets.token_urlsafe(40)
        service=relay.Server(("127.0.0.1",0),queue,client_key,
                            timeout=100,enable_playwright=True,
                            playwright_key=worker_key)
        thread=threading.Thread(target=service.serve_forever,daemon=True)
        thread.start()
        guard.callback(lambda: (service.shutdown(),thread.join(5),service.server_close()))
        base_url=f"http://127.0.0.1:{service.server_address[1]}"
        provider=base_url+"/v1"
        cfg={
            "model":{"default":relay.MODEL_CDP,"provider":"custom","base_url":provider},
            "toolsets":[],
            "agent":{"max_turns":1,"api_max_retries":0,"auto_recovery_cycles":0},
            "compression":{"enabled":False},
            "auxiliary":{"title_generation":{"enabled":False}},
            "fallback_providers":[]
        }
        (hermes_home/"config.yaml").write_text(json.dumps(cfg,indent=2),encoding="utf8")
        (hermes_home/".env").write_text(
            "OPENAI_BASE_URL="+provider+"\nOPENAI_API_KEY="+client_key+"\n",encoding="utf8")
        cli_env=clean_env(hermes_home,provider,client_key)

        edge=subprocess.Popen([str(edge_path()),"--headless=new",
                    "--remote-debugging-port=0","--remote-debugging-address=127.0.0.1",
                    f"--user-data-dir={browser_profile}","--disable-background-networking",
                    "--disable-extensions","--no-first-run","--no-default-browser-check",
                    "about:blank"],stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0))
        def clean_edge():
            if edge.poll() is None:
                edge.terminate()
                try:edge.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    edge.kill();edge.wait(timeout=8)
        guard.callback(clean_edge)
        port=await asyncio.to_thread(debug_port,browser_profile,edge)
        async with async_playwright() as pw:
            browser=await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                if len(browser.contexts)!=1:
                    raise AssertionError("isolated_edge_context_missing")
                context=browser.contexts[0]
                page=await context.new_page()
                async def offline_route(route):
                    if route.request.url==CHAT:
                        await route.fulfill(status=200,content_type="text/html",
                            body=ROOT_FIXTURE_HTML.replace("__CASE__",reply_mode))
                    else:
                        await route.abort()
                await page.route("**/*",offline_route)
                await page.goto(CHAT,wait_until="domcontentloaded",timeout=15000)
                # The browser never makes a real HTTP request to chatgpt.com.
                if client_kind=="telegram":
                    client_args=[str(cli),
                        str(ROOT/"planning"/"hermes_live_mcp_telegram_sdk_child.py")]
                else:
                    client_args=[str(cli),"-m","hermes_cli.main","-z",
                        "Return the literal marker LIVE_DOM_FIXTURE_OK without using tools.",
                        "--provider","custom","-m",relay.MODEL_CDP,"-t",""]
                cli_process=await asyncio.create_subprocess_exec(
                    *client_args,cwd=hermes_home,env=cli_env,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                try:
                    # Ensure the Hermes SDK actually submitted its HTTP model
                    # request before the MCP sidecar claims anything.
                    admitted=False
                    deadline=time.monotonic()+50
                    while time.monotonic()<deadline:
                        with queue.lock:
                            queued=queue.db.execute(
                                "SELECT COUNT(*) FROM requests WHERE state='queued'").fetchone()[0]
                        if queued>0:
                            admitted=True
                            break
                        if cli_process.returncode is not None:
                            raise AssertionError("cli_exited_before_model_submit")
                        await asyncio.sleep(0.2)
                    if not admitted:
                        raise AssertionError("hermes_cli_model_request_not_admitted")
                    worker_env=os.environ.copy()
                    worker_env["HERMES_CDP_LIVE_ENABLE"]="1"
                    worker_env["HERMES_CDP_LIVE_WORKER_KEY"]=worker_key
                    sidecar=await asyncio.create_subprocess_exec(
                        sys.executable,"-m","hermes_cdp_mcp_sidecar",
                        "--relay",base_url,"--cdp",f"http://127.0.0.1:{port}",
                        "--chat",CHAT,"--journal",str(journal),
                        "--timeout-seconds","35","--stop-after","1",
                        cwd=ROOT,env=worker_env,stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE)
                    try:
                        side_out,side_err=await asyncio.wait_for(sidecar.communicate(),timeout=75)
                        cli_out,cli_err=await asyncio.wait_for(cli_process.communicate(),timeout=55)
                        result=cli_out.decode("utf8",errors="replace")
                        if client_kind=="telegram":
                            try:
                                telegram_receipt=json.loads(
                                    result.strip().splitlines()[-1])
                            except (ValueError, IndexError):
                                telegram_receipt={}
                            accepted=(
                                telegram_receipt.get("ok") is True
                                and telegram_receipt.get("native_telegram_sdk_inbound") is True
                                and telegram_receipt.get("mock_telegram_send_result_ack") is True
                                and telegram_receipt.get("mock_reply_same_chat") is True
                                and telegram_receipt.get("mock_reply_same_topic") is True
                            )
                        else:
                            telegram_receipt={}
                            accepted="LIVE_DOM_FIXTURE_OK" in result
                        with queue.lock:
                            states=queue.db.execute("SELECT state FROM requests").fetchall()
                        clicks=await page.evaluate("window.sendCount")
                        report={
                            "ok":sidecar.returncode==0 and cli_process.returncode==0
                                and len(states)==1 and states[0]["state"]=="done"
                                and clicks==1 and accepted,
                            "client":"installed_Hermes_CLI" if client_kind=="cli"
                                     else "native_Hermes_Telegram_SDK",
                            "native_telegram_sdk_inbound":client_kind=="telegram",
                            "mock_telegram_api_ack":telegram_receipt.get(
                                "mock_telegram_send_result_ack",False),
                            "mock_reply_same_chat":telegram_receipt.get(
                                "mock_reply_same_chat",False),
                            "mock_reply_same_topic":telegram_receipt.get(
                                "mock_reply_same_topic",False),
                            "synthetic_forum_source_bound":telegram_receipt.get(
                                "source_thread_bound",False),
                            "synthetic_topic_metadata":telegram_receipt.get(
                                "mock_outbound_metadata"),
                            "relay":"real_ephemeral_http",
                            "mcp":"actual_stdio_sidecar_and_server",
                            "browser":"real_Edge_connect_over_cdp",
                            "model":relay.MODEL_CDP,
                            "reply_mode":reply_mode,
                            "browser_sends":clicks,
                            "relay_request_count":len(states),
                            "relay_state":states[0]["state"] if len(states)==1 else "unexpected",
                            "hermes_got_model_answer":accepted,
                            "cli_exit_code":cli_process.returncode,
                            "sidecar_exit_code":sidecar.returncode,
                            "web_extension_used":False,
                            "actual_chatgpt_http_requests":0,
                            "telegram_messages_sent":0,
                            "live_browser_profile_modified":False,
                            "elapsed_seconds":round(time.monotonic()-begin,2)
                        }
                        print(json.dumps(report,ensure_ascii=False,sort_keys=True))
                        if not report["ok"]:
                            raise AssertionError("hermes_mcp_cdp_full_model_cycle_failed")
                    finally:
                        if sidecar.returncode is None:
                            sidecar.kill();await sidecar.communicate()
                finally:
                    if cli_process.returncode is None:
                        cli_process.kill();await cli_process.communicate()
            finally:
                await browser.close()

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--client",choices=("cli","telegram"),default="cli")
    parser.add_argument("--reply-mode",
                        choices=("ready","fenced_json","rendered_code","streaming_json"),
                        default="ready")
    options=parser.parse_args()
    venv=ROOT/".venv-cdp"/"Scripts"/"python.exe"
    if Path(sys.executable).resolve()!=venv.resolve():
        if not venv.is_file():
            print('{"ok":false,"reason":"cdp_venv_missing"}')
            raise SystemExit(2)
        try:
            r=subprocess.run([str(venv),str(Path(__file__).resolve()),"--client",options.client,"--reply-mode",options.reply_mode],
                cwd=ROOT,capture_output=True,text=True,encoding="utf8",
                errors="replace",timeout=190)
        except subprocess.TimeoutExpired:
            print('{"ok":false,"reason":"isolated_model_cycle_timeout"}')
            raise SystemExit(2)
        print(r.stdout.strip()[-6000:] or '{"ok":false,"reason":"no_receipt"}')
        if r.returncode:
            print(r.stderr.strip()[-1500:],file=sys.stderr)
        raise SystemExit(r.returncode)
    try:
        asyncio.run(run(options.client,options.reply_mode))
    except Exception as exc:
        import traceback
        frames=traceback.extract_tb(exc.__traceback__)
        last=frames[-1] if frames else None
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,
                          "source":Path(last.filename).name if last else None,
                          "line":last.lineno if last else None,
                          "reason":"isolated_full_model_cycle_failed"},sort_keys=True))
        raise SystemExit(1)
