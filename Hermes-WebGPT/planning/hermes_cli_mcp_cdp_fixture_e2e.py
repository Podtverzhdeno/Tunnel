"""Installed Hermes CLI -> local Relay -> MCP stdio -> real Edge fixture -> Hermes CLI.

The model provider is the *existing* locally instantiated Relay; no production
Gateway, Telegram token, account profile, ChatGPT session or external API used.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"planning"))
import relay
from cdp_relay_fixture_e2e import run_server
from cdp_fixture_server import create_server
from cdp_fixture_client import verify_fixture_health

SDK=ROOT/"hermes_web_cdp_mcp.py"
MODEL=relay.MODEL_CDP

def clean_env(home:Path,base:str,key:str):
    keep={"PATH","PATHEXT","SYSTEMROOT","WINDIR","COMSPEC","TEMP","TMP",
          "APPDATA","LOCALAPPDATA","USERPROFILE","PYTHONPATH","VIRTUAL_ENV",
          "PYTHONHOME"}
    env={k:v for k,v in os.environ.items() if k.upper() in keep}
    env.update({"HERMES_HOME":str(home), "OPENAI_BASE_URL":base,
                "OPENAI_API_KEY":key, "NO_PROXY":"127.0.0.1,localhost",
                "no_proxy":"127.0.0.1,localhost","PYTHONIOENCODING":"utf-8",
                "HERMES_INFERENCE_PROVIDER":"custom",
                "HERMES_INFERENCE_MODEL":MODEL})
    return env

async def run():
    installed=os.environ.get("LOCALAPPDATA")
    if not installed:
        raise AssertionError("installed_Hermes_path_missing")
    cli_python=Path(installed)/"hermes"/"hermes-agent"/"venv"/"Scripts"/"python.exe"
    if not cli_python.is_file():
        raise AssertionError("installed_Hermes_python_missing")
    started=time.monotonic()
    with ExitStack() as scope:
        directory=Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="hermes-cli-mcp-cdp-e2e-")))
        profile=directory/"hermes";profile.mkdir()
        journal=directory/"journal";journal.mkdir()
        queue=relay.Queue(directory/"relay.sqlite3")
        scope.callback(queue.close)
        fixture=create_server()
        ft=run_server(fixture)
        scope.callback(lambda:(fixture.shutdown(),ft.join(5),fixture.server_close()))
        fixture_url=f"http://127.0.0.1:{fixture.server_address[1]}/"
        verify_fixture_health(fixture_url)
        client_key=secrets.token_urlsafe(40)
        worker_key=secrets.token_urlsafe(40)
        service=relay.Server(("127.0.0.1",0),queue,client_key,timeout=95,
                             enable_playwright=True,playwright_key=worker_key)
        st=run_server(service)
        scope.callback(lambda:(service.shutdown(),st.join(5),service.server_close()))
        relay_base=f"http://127.0.0.1:{service.server_address[1]}"
        provider_base=relay_base+"/v1"
        config={"model":{"default":MODEL,"provider":"custom",
                          "base_url":provider_base},
                "toolsets":[],
                "agent":{"max_turns":1,"api_max_retries":0,
                         "auto_recovery_cycles":0},
                "compression":{"enabled":False},
                "auxiliary":{"title_generation":{"enabled":False}},
                "fallback_providers":[]}
        (profile/"config.yaml").write_text(json.dumps(config,indent=2),encoding="utf8")
        (profile/".env").write_text("OPENAI_BASE_URL="+provider_base+"\nOPENAI_API_KEY="+client_key+"\n",encoding="utf8")
        env=clean_env(profile,provider_base,client_key)
        worker_environment={"HERMES_CDP_FIXTURE_ONLY":"1",
                            "HERMES_CDP_FIXTURE_WORKER_KEY":worker_key}
        # Only test process can loosen chat_url to the local fixture. The
        # installed/live relay remains unchanged and still requires chatgpt.com.
        with patch.object(relay,"chat_url",side_effect=lambda url:url==fixture_url):
            proc=await asyncio.create_subprocess_exec(
                str(cli_python),"-m","hermes_cli.main","-z",
                "Answer with the literal marker LOCAL_FIXTURE_OK. Do not use tools.",
                "--provider","custom","-m",MODEL,"-t","",
                cwd=profile,env=env,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                deadline=time.monotonic()+70
                queued=False
                while time.monotonic()<deadline:
                    with queue.lock:
                        count=queue.db.execute(
                            "SELECT COUNT(*) FROM requests WHERE state='queued'").fetchone()[0]
                    if count>0:
                        queued=True
                        break
                    if proc.returncode is not None:
                        raise AssertionError("Hermes_CLI_exited_before_relay_queue")
                    await asyncio.sleep(.2)
                if not queued:
                    raise AssertionError("Hermes_CLI_did_not_submit_inference")
                params=StdioServerParameters(command=sys.executable,args=[str(SDK)],
                                             env=worker_environment)
                async with stdio_client(params) as (reader,writer):
                    async with ClientSession(reader,writer) as session:
                        await session.initialize()
                        offered=await session.list_tools()
                        assert {tool.name for tool in offered.tools} == {
                            "hermes_web_transport_status",
                            "hermes_web_process_fixture_once"}
                        tool=await session.call_tool(
                            "hermes_web_process_fixture_once",
                            arguments={"relay_url":relay_base,"fixture_url":fixture_url,
                                       "journal_dir":str(journal),"case":"ready"})
                        assert not tool.isError, "mcp_fixture_tool_failed"
                        result=json.loads(tool.content[0].text)
                        assert result["outcome"]=="done" and result["send_count"]==1, result
                stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=55)
                decoded=stdout.decode("utf8",errors="replace")
                with queue.lock:
                    rows=queue.db.execute("SELECT state FROM requests").fetchall()
                assert len(rows)==1 and rows[0]["state"]=="done", rows
                report={"ok":proc.returncode==0 and "LOCAL_FIXTURE_OK" in decoded,
                        "cli":"actual_installed_Hermes_CLI",
                        "mcp":"actual_mcp_1_30_ClientSession_stdio",
                        "browser":"Edge_Playwright_fixture",
                        "model_alias":MODEL,
                        "relay_state":rows[0]["state"],
                        "browser_sends":result["send_count"],
                        "cli_received_fixture_marker":"LOCAL_FIXTURE_OK" in decoded,
                        "cli_exit_code":proc.returncode,
                        "telegram_messages_sent":0,"chatgpt_web_requests":0,
                        "live_runtime_modified":False,
                        "elapsed_seconds":round(time.monotonic()-started,2)}
                print(json.dumps(report,sort_keys=True),flush=True)
                if not report["ok"]:
                    raise AssertionError("Hermes_CLI_MCP_integration_not_verified")
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.communicate()

if __name__=="__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        import traceback
        where=traceback.extract_tb(exc.__traceback__)
        last=where[-1] if where else None
        print(json.dumps({"ok":False,"error_type":type(exc).__name__,
                          "source":Path(last.filename).name if last else None,
                          "line":last.lineno if last else None,
                          "reason":"Hermes_CLI_MCP_fixture_E2E_failed"},sort_keys=True))
        raise SystemExit(1)
