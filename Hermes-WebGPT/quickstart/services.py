"""Windows one-click service guard for existing Hermes project processes.

NO Telegram/ChatGPT messages are sent by this supervisor. The launcher checks
process identity, existing receipts and Relay queue before a new process starts.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class StartupBlocked(RuntimeError):
    pass


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _http_relay():
    req = urllib.request.Request("http://127.0.0.1:18765/health")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=2) as response:
            doc=json.load(response)
            return doc.get("service")=="hermes-webgpt-relay" and doc.get("protocol")==1
    except Exception:
        return False


def _processes():
    if os.name != "nt":
        raise StartupBlocked("windows_only")
    # No command lines or credentials ever emitted to stdout/logs.
    ps = ("Get-CimInstance Win32_Process | "
          "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    p=subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",ps],
                     capture_output=True,text=True,timeout=30)
    if p.returncode!=0:
        raise StartupBlocked("process_inventory_unavailable")
    raw=p.stdout.strip()
    arr=json.loads(raw) if raw and raw!="null" else []
    return arr if isinstance(arr,list) else [arr]


def _port_busy(port):
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        s.settimeout(.4)
        return s.connect_ex(("127.0.0.1",port))==0
    finally:
        s.close()


def _queue_states():
    path=ROOT/"data"/"relay.sqlite3"
    if not path.exists():
        return {}
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=4) as db:
        db.execute("PRAGMA query_only = ON")
        states=dict(db.execute("SELECT state,COUNT(*) FROM requests GROUP BY state"))
    if any(state not in {"done","blocked","uncertain"} for state in states):
        raise StartupBlocked("relay_in_flight_queue")
    return states


def _gateway_pid():
    f=ROOT/"profile"/"gateway.pid"
    if not f.exists():
        return None
    try:
        value=f.read_text(encoding="utf8").strip()
        if value.startswith("{"):
            value=json.loads(value).get("pid")
        ident=int(value)
        return ident if ident>0 else None
    except (ValueError,TypeError,KeyError):
        raise StartupBlocked("gateway_pidfile_unreadable")


def _gateway_env_available():
    f=ROOT/"profile"/".env"
    fields={}
    if f.is_file():
        for line in f.read_text(encoding="utf8").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k,v=line.split("=",1)
            if k.strip().isidentifier():
                fields[k.strip()]=v.strip().strip('"').strip("'")
    # On a new PC, the A4 setup wizard can store the bot token as a
    # user-scoped DPAPI secret. The PowerShell launcher decrypts it only
    # in memory and passes it via an inherited child environment.
    # Preserve an already configured profile token.
    if not fields.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_BOT_TOKEN"):
        fields["TELEGRAM_BOT_TOKEN"]=os.environ["TELEGRAM_BOT_TOKEN"]
    if not fields.get("TELEGRAM_BOT_TOKEN"):
        raise StartupBlocked("telegram_bot_token_missing")
    return fields


def _owned_processes(rows, marker):
    real=str((ROOT/marker).resolve()).casefold()
    return [p for p in rows if real in str(p.get("CommandLine") or "").casefold()]


def relay_plan(rows=None):
    if _http_relay():
        return {"action":"already_running","service":"relay","port":18765}
    if _port_busy(18765):
        raise StartupBlocked("relay_port_owned_by_unknown_service")
    all_rows=_processes() if rows is None else rows
    if _owned_processes(all_rows,"relay.py"):
        raise StartupBlocked("relay_process_without_health")
    states=_queue_states()
    receipt=ROOT/"bridge_process.json"
    old_pid=None
    if receipt.exists():
        old=json.loads(receipt.read_text(encoding="utf8"))
        old_pid=old.get("pid")
        if type(old_pid) is not int or old_pid<=0:
            raise StartupBlocked("relay_receipt_invalid")
        if any(p.get("ProcessId")==old_pid for p in all_rows):
            raise StartupBlocked("relay_recorded_pid_still_alive")
    from importlib.util import spec_from_file_location
    # File hash used by the caller before and after the preflight.
    return {"action":"start","service":"relay","old_pid":old_pid,
            "queue_states":states,"relay_sha":_sha(ROOT/"relay.py"),
            "starter_sha":_sha(ROOT/"start_bridge.py"),
            "receipt_sha":_sha(receipt) if receipt.exists() else None}


def gateway_plan(rows=None):
    all_rows=_processes() if rows is None else rows
    active=_owned_processes(all_rows,"gateway_runner.py")
    if len(active)>2:
        raise StartupBlocked("multiple_gateway_processes")
    pid=_gateway_pid()
    if active:
        if pid is None:
            raise StartupBlocked("gateway_process_without_pidfile")
        child=next((p for p in active if p.get("ProcessId")==pid),None)
        if child is None:
            raise StartupBlocked("gateway_pid_process_mismatch")
        launcher_pid=None
        if len(active)==2:
            parent=next((p for p in active if p.get("ProcessId")!=pid),None)
            if parent is None:
                raise StartupBlocked("gateway_pid_process_mismatch")
            receipt=ROOT/"gateway_launch.json"
            try:
                documented_parent=json.loads(receipt.read_text(encoding="utf8")).get("pid")
            except (OSError,ValueError,AttributeError):
                raise StartupBlocked("gateway_parent_receipt_unavailable")
            if (parent.get("ProcessId")!=documented_parent or
                child.get("ParentProcessId")!=documented_parent):
                raise StartupBlocked("gateway_parent_child_not_verified")
            launcher_pid=documented_parent
        return {"action":"already_running","service":"gateway",
                "pid":pid,"launcher_pid":launcher_pid}
    if pid is not None and any(p.get("ProcessId")==pid for p in all_rows):
        raise StartupBlocked("gateway_pid_reused")
    profile=ROOT/"profile"
    if not profile.is_dir():
        raise StartupBlocked("gateway_profile_missing")
    _gateway_env_available()
    runner=ROOT/"gateway_runner.py"
    if not runner.is_file():
        raise StartupBlocked("gateway_runner_missing")
    receipt=ROOT/"gateway_launch.json"
    old_pid=None
    if receipt.exists():
        old=json.loads(receipt.read_text(encoding="utf8"))
        old_pid=old.get("pid")
        if type(old_pid) is not int or old_pid<=0:
            raise StartupBlocked("gateway_receipt_invalid")
        if any(p.get("ProcessId")==old_pid for p in all_rows):
            raise StartupBlocked("gateway_recorded_pid_exists")
    state=profile/"gateway_state.json"
    if state.is_file():
        snapshot=json.loads(state.read_text(encoding="utf8"))
        if snapshot.get("active_agents") not in (None,0):
            raise StartupBlocked("gateway_active_agents_nonzero")
    return {"action":"start","service":"gateway","old_pid":old_pid,
            "runner_sha":_sha(runner),
            "receipt_sha":_sha(receipt) if receipt.exists() else None}


def _backup_receipt(name):
    receipt=ROOT/name
    if not receipt.exists():
        return None
    stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
    dest=ROOT/f"{name}.before_oneclick_{stamp}.bak"
    if dest.exists():
        raise StartupBlocked("receipt_backup_exists")
    receipt.rename(dest)
    return dest.name


def start_relay(preflight):
    if relay_plan()!=preflight:
        raise StartupBlocked("relay_preflight_drift")
    _backup_receipt("bridge_process.json")
    sys.path.insert(0,str(ROOT))
    import start_bridge
    start_bridge.main()
    if not _http_relay():
        raise StartupBlocked("relay_health_not_ready")
    return {"ok":True,"service":"relay","state":"started",
            "queue_states":_queue_states(),"capacity_file":str(ROOT/"relay_capacity.json")}


def start_gateway(preflight):
    if gateway_plan()!=preflight:
        raise StartupBlocked("gateway_preflight_drift")
    fields=_gateway_env_available()
    env=dict(os.environ)
    env.update(fields)
    env["HERMES_HOME"]=str(ROOT/"profile")
    env["NO_PROXY"]="127.0.0.1,localhost"
    env["no_proxy"]="127.0.0.1,localhost"
    env["PYTHONIOENCODING"]="utf-8"
    _backup_receipt("gateway_launch.json")
    with open(ROOT/"gateway_console.log","ab",buffering=0) as out:
        flags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0
        proc=subprocess.Popen([sys.executable,str(ROOT/"gateway_runner.py")],
                              cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                              stdout=out,stderr=out,creationflags=flags,
                              start_new_session=os.name!="nt",close_fds=True)
    receipt=ROOT/"gateway_launch.json"
    # Never serialize process environment, tokens or model data.
    data={"pid":proc.pid,"created_by":"Hermes-OneClick","state":"STARTING_UNVERIFIED"}
    with receipt.open("x",encoding="utf8") as f:
        json.dump(data,f)
    for _ in range(30):
        if proc.poll() is not None:
            raise StartupBlocked("gateway_process_exited")
        native_pid=_gateway_pid()
        if native_pid is not None:
            owned=_owned_processes(_processes(),"gateway_runner.py")
            matched=[p for p in owned if p.get("ProcessId")==native_pid
                     and p.get("ParentProcessId")==proc.pid]
            if len(owned)==2 and len(matched)==1:
                data["state"]="PROCESS_PID_CONFIRMED"
                data["gateway_pid"]=native_pid
                receipt.write_text(json.dumps(data),encoding="utf8")
                return {"ok":True,"service":"gateway","state":"process_pid_confirmed",
                        "pid":native_pid,"launcher_pid":proc.pid,
                        "telegram_delivery_verified":False}
        time.sleep(.4)
    # It MAY still be starting. Report uncertainty; never try a duplicate.
    return {"ok":False,"service":"gateway","state":"STARTING_UNVERIFIED",
            "pid":proc.pid,"telegram_delivery_verified":False}


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (1,2) or argv[0] not in ("relay","gateway") or (len(argv)==2 and argv[1]!="--apply"):
        print(json.dumps({"ok":False,"error":"usage: services.py relay|gateway [--apply]"}))
        return 2
    service=argv[0]
    try:
        preflight=relay_plan() if service=="relay" else gateway_plan()
        if len(argv)==1 or preflight["action"]=="already_running":
            print(json.dumps({"ok":True,"preflight":preflight,"mutated":False}))
            return 0
        result=start_relay(preflight) if service=="relay" else start_gateway(preflight)
        print(json.dumps(result),flush=True)
        return 0 if result["ok"] else 3
    except (StartupBlocked,OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print(json.dumps({"ok":False,"service":service,"code":str(exc)[:100],
                          "no_replay":True,"manual_reconciliation_required":True}),flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
