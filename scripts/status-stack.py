"""Read-only, privacy-minimal readiness report for a portable Tunnel install.

This command does not create jobs, open chats, start services, print secrets,
or invoke the Windows P085 broker.
Usage: python scripts/status-stack.py [--root PATH] [--mcp-port 8765]
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
MAX_DIAG_BYTES=20000


def _json_file(path: Path, max_bytes=MAX_DIAG_BYTES):
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def _run_preview(path: Path):
    if not path.is_file() or path.is_symlink():
        return {"status":"source_missing"}
    try:
        completed=subprocess.run([sys.executable,str(path),"preview"],
            cwd=str(path.parent),stdin=subprocess.DEVNULL,
            capture_output=True,text=True,encoding="utf-8",errors="replace",
            timeout=12)
        if completed.returncode:
            return {"status":"preview_failed","exit_code":completed.returncode}
        data=json.loads(completed.stdout)
        if not isinstance(data,dict) or data.get("ok") is not True:
            return {"status":"preview_unavailable"}
        return data
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"status":"preview_unavailable"}


def _chain2_triage_status(root: Path):
    script=root/"scripts"/"chain2-triage.py"
    if script.is_symlink() or not script.is_file():
        return {"status":"source_missing"}
    try:
        result=subprocess.run([sys.executable,str(script)],
            cwd=str(script.parent),stdin=subprocess.DEVNULL,
            capture_output=True,text=True,encoding="utf-8",errors="replace",
            timeout=20)
        if result.returncode:
            return {"status":"triage_unavailable"}
        if len(result.stdout)>100000:
            return {"status":"triage_too_large"}
        doc=json.loads(result.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"status":"triage_unavailable"}
    if not isinstance(doc,dict) or doc.get("ok") is not True or doc.get("read_only") is not True:
        return {"status":"triage_invalid"}
    summary=doc.get("summary")
    if type(summary) is not dict or type(summary.get("active_categories")) is not dict:
        return {"status":"triage_invalid"}
    kinds=("browser_delivery_uncertain","handoff_invalid",
           "handoff_ready_for_role_review","worker_evidence_incomplete",
           "handoff_request_pending_ack")
    try:
        counts={}
        for k in kinds:
            value=summary["active_categories"].get(k,0)
            if type(value) is not int or value<0:
                raise ValueError()
            counts[k]=value
        active=summary["active"]
        if type(active) is not int or active<0 or sum(counts.values())!=active:
            raise ValueError()
    except (TypeError,ValueError,KeyError):
        return {"status":"triage_invalid"}
    return {"status":"needs_review" if active else "no_active_jobs",
            "active":active,"categories":counts,
            "manual_browser_checks":counts["browser_delivery_uncertain"]}


def _mcp_status(root: Path,port: int):
    host=root/"Local-Workspace-Extensions"/"mcp-host-source"
    token_path=host/".local-mcp-state"/"observer-token"
    header_path=host/".local-mcp-state"/"observer-authorization-header"
    if not host.joinpath("manage.py").is_file():
        return {"status":"source_missing"}
    if any(p.is_symlink() for p in (token_path,header_path)):
        return {"status":"local_auth_invalid"}
    try:
        if (not token_path.is_file() or not header_path.is_file()
            or token_path.stat().st_size>150 or header_path.stat().st_size>150):
            return {"status":"local_auth_not_initialized"}
        key=token_path.read_text("ascii")
        header=header_path.read_text("ascii")
        import re
        if (re.fullmatch("[A-Za-z0-9_-]{43,128}",key) is None
            or header!="Bearer "+key):
            return {"status":"local_auth_invalid"}
    except (OSError,UnicodeError):
        return {"status":"local_auth_invalid"}
    req_data=json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
         "params":{"protocolVersion":"2025-11-25"}}).encode("utf-8")
    req=urllib.request.Request(f"http://127.0.0.1:{port}/mcp",data=req_data,
             headers={"Authorization":header,"Content-Type":"application/json"},
             method="POST")
    try:
        with urllib.request.urlopen(req,timeout=1.5) as response:
            data=json.load(response)
        if data.get("result",{}).get("serverInfo",{}).get("name")=="Local Workspace MCP":
            return {"status":"ready","auth_valid":True,"profile":"observer",
                    "port":port}
        return {"status":"unexpected_response","port":port}
    except urllib.error.HTTPError as exc:
        return {"status":"auth_rejected" if exc.code==401 else "http_error",
                "http_status":exc.code,"port":port}
    except (OSError,ValueError,TimeoutError):
        return {"status":"not_running","port":port}


def _bridge_status(root: Path,port: int):
    base=root/"chat-launch-smoke-desktop-test"/"web-companion"
    state=base/"bridge-state"
    token=state/"token"
    if not (base/"bridge.py").is_file():
        return {"status":"source_missing"}
    if token.is_symlink() or not token.is_file():
        return {"status":"not_initialized"}
    try:
        if not 32<=token.stat().st_size<=256:
            return {"status":"local_key_invalid"}
        key=token.read_text("utf-8").strip()
        if len(key)<32:return {"status":"local_key_invalid"}
    except (OSError,UnicodeError):
        return {"status":"local_key_invalid"}
    request=urllib.request.Request(f"http://127.0.0.1:{port}/v1/health",
                                   headers={"X-Chat-Smoke-Key":key})
    try:
        with urllib.request.urlopen(request,timeout=1.5) as response:
            payload=json.load(response)
        return {"status":"ready" if payload.get("ok") is True else "invalid_health",
                "port":port}
    except urllib.error.HTTPError as exc:
        return {"status":"auth_rejected" if exc.code in (401,403) else "http_error",
                "http_status":exc.code,"port":port}
    except (OSError,ValueError,TimeoutError):
        return {"status":"not_running","port":port}


def _extension_status(root: Path):
    path=root/"chat-launch-smoke-desktop-test"/"web-companion"/"bridge-state"/"diagnostics"/"extension.json"
    data=_json_file(path,4096)
    if not isinstance(data,dict):
        return {"status":"no_recent_heartbeat"}
    try:
        stamp=datetime.fromisoformat(str(data["at"]).replace("Z","+00:00"))
        if stamp.tzinfo is None:raise ValueError()
        now=datetime.now(timezone.utc)
        if stamp.astimezone(timezone.utc)>now+timedelta(seconds=60):
            return {"status":"invalid_future_heartbeat"}
        age=max(0,(now-stamp.astimezone(timezone.utc)).total_seconds())
    except (ValueError,TypeError,KeyError,OverflowError):
        return {"status":"invalid_heartbeat"}
    import re
    version=data.get("version")
    if type(version) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}",version):
        version=None
    return {"status":"recent" if age<=180 else "stale",
            "heartbeat_age_seconds":round(age),"version":version}


def summary(root: Path,mcp_port=8765,bridge_port=18763):
    mcp=_mcp_status(root,mcp_port)
    bridge=_bridge_status(root,bridge_port)
    extension=_extension_status(root)
    controller=root/"chat-launch-smoke-desktop-test"/"web-companion"
    chain2=_run_preview(controller/"chain_campaign.py")
    chain3=_run_preview(controller/"chain3_continuous.py")
    chain2_triage=_chain2_triage_status(root)
    if chain2.get("ok") is not True:
        chain2_state=chain2.get("status","unavailable")
    elif not chain2.get("armed"):
        chain2_state="not_owner_armed"
    elif chain2.get("held"):
        chain2_state="admission_held"
    elif chain2.get("ready",0)>0:
        chain2_state="ready"
    elif chain2.get("states",{}).get("active",0)>0:
        chain2_state="blocked_active"
    else:
        chain2_state="waiting_or_blocked"
    chain2_view={"status":chain2_state,
                 "done":chain2.get("states",{}).get("done"),
                 "active":chain2.get("states",{}).get("active"),
                 "ready":chain2.get("ready"),
                 "owner_armed":chain2.get("armed")}
    chain3_view={"status":("ready_for_admission" if chain3.get("ok")
                   and chain3.get("owner_armed") and chain3.get("chain2_fully_accepted")
                   and chain3.get("effective_mode")!="blocked"
                   and not chain3.get("admission_held") else "blocked"),
                 "selected_mode":chain3.get("selected_mode"),
                 "effective_mode":chain3.get("effective_mode"),
                 "owner_armed":chain3.get("owner_armed"),
                 "chain2_fully_accepted":chain3.get("chain2_fully_accepted")}
    actions=[]
    if mcp["status"] in ("local_auth_not_initialized","local_auth_invalid"):
        actions.append("Run: py -3 scripts/observer_auth.py init (inspect existing invalid files first)")
    elif mcp["status"]=="not_running":
        actions.append("Start: py -3 scripts/observer_auth.py serve --port 8765")
    elif mcp["status"]=="auth_rejected":
        actions.append("MCP bearer mismatch: check the process and private header file")
    if bridge["status"]=="not_initialized":
        actions.append("From web-companion: py -3 setup_bridge.py; py -3 build_clean_extension.py")
    elif bridge["status"]=="not_running":
        actions.append("From web-companion: py -3 start_bridge.py")
    if extension["status"] in ("stale","no_recent_heartbeat"):
        actions.append("Check the unpacked Chromium extension and ChatGPT login")
    if chain3_view["effective_mode"]=="blocked":
        actions.append("Trusted Chain3 requires independently verified OS-protected P085 broker")
    if chain2_triage.get("status")=="needs_review":
        counts=chain2_triage["categories"]
        if counts["browser_delivery_uncertain"]:
            actions.append("Manually inspect uncertain Chain2 browser sends; never replay UNKNOWN")
        if counts["handoff_invalid"]:
            actions.append("Review invalid Chain2 handoff artifacts before successor admission")
        if counts["handoff_ready_for_role_review"]:
            actions.append("Have the authorized parent role review valid handoff and request successor")
    if chain3_view["chain2_fully_accepted"] is False:
        actions.append("Review Chain2 incomplete and ambiguous active reports before Chain3")
    return {"ok":True,"read_only":True,"mcp":mcp,"bridge":bridge,"extension":extension,
            "chain2":chain2_view,"chain2_triage":chain2_triage,"chain3":chain3_view,
            "tunnel":{"status":"not_checked","note":"Run tunnel-client doctor with owner's profile"},
            "next_actions":actions[:12],
            "full_autonomous_ready":False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--mcp-port",type=int,default=8765)
    p.add_argument("--bridge-port",type=int,default=18763)
    a=p.parse_args()
    if any(type(port) is not int or port<1024 or port>65535
           for port in (a.mcp_port,a.bridge_port)):
        print(json.dumps({"ok":False,"error":"invalid_port"}))
        return 1
    print(json.dumps(summary(a.root,a.mcp_port,a.bridge_port),ensure_ascii=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
