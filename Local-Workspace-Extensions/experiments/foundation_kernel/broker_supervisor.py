"""Durable, bounded broker supervisor; never automatically repeats an operation.

Only spawns the *fixed* broker_named_pipe.py implementation with an exact,
bounded argument contract. The supervisor itself is an experimental launcher,
NOT the installed Local Workspace MCP, a Windows service, an owner approval,
or a production OS security boundary.

A restart means launching a NEW LISTENER. It never resends an outbox message,
replays a command or transitions a journal state. Clients must use signed
read-only QUERY_COMMAND_STATUS to reconcile unknown effects.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone

try:
    from .broker_stdio import load_pinned_policy
    from .signed_policy import verify_signed_policy
    from .windows_isolation import current_user_sid
    from .win_pipe_ipc import _name, _validate_sid
except ImportError:
    from broker_stdio import load_pinned_policy
    from signed_policy import verify_signed_policy
    from windows_isolation import current_user_sid
    from win_pipe_ipc import _name, _validate_sid

SCHEMA="broker-supervisor-attempts-v1"
DB_NAME="broker_supervisor_attempts.sqlite3"
LOCK_NAME="broker_supervisor.lock"
MAX_AUDIT_ROWS=4096
MAX_SPAWNS=12
MAX_CHILD_OUTPUT=8192
TRANSIENT_ERRORS=frozenset({
    "PIPE_CONNECT_TIMEOUT","PIPE_READ_TIMEOUT",
    "PIPE_READ_FAILED","PIPE_SEND_FAILED","PIPE_WRITE_TIMEOUT",
    "PIPE_CONNECT_FAILED","PIPE_CLIENT_CONNECTION_DENIED",
})

class SupervisorError(ValueError):
    pass


def _require_root(root: Path) -> Path:
    if root.is_symlink() or getattr(root,"is_junction",lambda:False)():
        raise SupervisorError("SUPERVISOR_ROOT_LINK_DENIED")
    if not root.is_dir():
        raise SupervisorError("SUPERVISOR_ROOT_MISSING")
    return root.resolve(strict=True)


@contextmanager
def single_instance(root: Path):
    """Process-lifetime nonblocking Win32 file lock; not a security ACL."""
    if sys.platform!="win32":
        raise SupervisorError("SUPERVISOR_WINDOWS_ONLY")
    import msvcrt
    path=root/LOCK_NAME
    if path.is_symlink() or getattr(path,"is_junction",lambda:False)():
        raise SupervisorError("SUPERVISOR_LOCK_LINK_DENIED")
    mode="r+b" if path.exists() else "w+b"
    handle=open(path,mode,buffering=0)
    try:
        if os.fstat(handle.fileno()).st_size==0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:
            raise SupervisorError("SUPERVISOR_ALREADY_RUNNING") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    finally:
        handle.close()


class AttemptLedger:
    """SQLite WAL/FULL restart budget that survives supervisor crashes.

    Records an attempt BEFORE starting its child. A process crash can
    consume a budget slot, but can never make the budget more permissive.
    No child command or signature is recorded in the audit table.
    """
    def __init__(self,root: Path,*,window_seconds=300,max_window_attempts=5):
        if (type(window_seconds) is not int or not 10<=window_seconds<=86400
            or type(max_window_attempts) is not int or
            not 1<=max_window_attempts<=20):
            raise SupervisorError("SUPERVISOR_BUDGET_INVALID")
        self.root=_require_root(root)
        self.window=window_seconds
        self.limit=max_window_attempts
        p=self.root/DB_NAME
        if p.is_symlink() or getattr(p,"is_junction",lambda:False)():
            raise SupervisorError("SUPERVISOR_DB_LINK_DENIED")
        self.db=sqlite3.connect(str(p),timeout=3,isolation_level=None)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS supervisor_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS supervisor_attempts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','FINISHED')),
                    reason TEXT,
                    child_returncode INTEGER,
                    CHECK((state='PENDING' AND reason IS NULL AND child_returncode IS NULL)
                      OR (state='FINISHED' AND reason IS NOT NULL))
                );
            """)
            row=self.db.execute(
                "SELECT schema FROM supervisor_meta WHERE singleton=1").fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO supervisor_meta(singleton,schema) VALUES(1,?)",
                    (SCHEMA,))
            elif row!=(SCHEMA,):
                raise SupervisorError("SUPERVISOR_LEDGER_SCHEMA_MISMATCH")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def acquire(self,now:int)->int:
        if type(now) is not int or not 1<=now<=2**40:
            raise SupervisorError("SUPERVISOR_CLOCK_INVALID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            latest=self.db.execute(
                "SELECT MAX(started_at), COUNT(*) FROM supervisor_attempts"
            ).fetchone()
            if latest[0] is not None and now<latest[0]:
                raise SupervisorError("SUPERVISOR_CLOCK_ROLLBACK")
            if latest[1]>=MAX_AUDIT_ROWS:
                raise SupervisorError("SUPERVISOR_AUDIT_CAPACITY_REACHED")
            active=self.db.execute(
                "SELECT COUNT(*) FROM supervisor_attempts "
                "WHERE started_at > ?",
                (now-self.window,)).fetchone()[0]
            if active>=self.limit:
                raise SupervisorError("SUPERVISOR_CIRCUIT_OPEN")
            cursor=self.db.execute(
                "INSERT INTO supervisor_attempts(started_at,state) "
                "VALUES(?,'PENDING')",(now,))
            self.db.execute("COMMIT")
            return cursor.lastrowid
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def finish(self,attempt_id:int,reason:str,returncode:int|None):
        if (type(attempt_id) is not int or attempt_id<=0 or
            type(reason) is not str or len(reason)>96 or
            not reason or any(c not in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in reason)):
            raise SupervisorError("SUPERVISOR_COMPLETION_INVALID")
        if returncode is not None and (type(returncode) is not int or
                                       not -2**31<=returncode<=2**31-1):
            raise SupervisorError("SUPERVISOR_EXIT_CODE_INVALID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            changed=self.db.execute(
                "UPDATE supervisor_attempts SET state='FINISHED',"
                "reason=?,child_returncode=? WHERE id=? AND state='PENDING'",
                (reason,returncode,attempt_id)).rowcount
            if changed!=1:
                raise SupervisorError("SUPERVISOR_ATTEMPT_ALREADY_SETTLED")
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def report(self):
        rows=self.db.execute(
            "SELECT state,reason,child_returncode FROM supervisor_attempts "
            "ORDER BY id").fetchall()
        return {
            "attempts":len(rows),
            "pending":sum(x[0]=="PENDING" for x in rows),
            "completed":sum(x[0]=="FINISHED" for x in rows),
            "retries_not_commands":True,
            "automatic_command_replay_permitted":False,
        }


def classify(returncode:int,output:bytes,*,timed_out:bool=False)->str:
    """Parse bounded CHILD status; never use status to authorize a command."""
    if timed_out:
        return "CHILD_WATCHDOG_TIMEOUT"
    if len(output)>MAX_CHILD_OUTPUT:
        return "CHILD_OUTPUT_TOO_LARGE"
    if not output and returncode not in (0,None):
        return "CHILD_PROCESS_CRASHED"
    try:
        rows=[json.loads(x) for x in output.splitlines()]
    except (ValueError,UnicodeError):
        return "CHILD_STATUS_INVALID"
    if (not rows or any(type(x) is not dict or
                       type(x.get("ok")) is not bool for x in rows)):
        return "CHILD_STATUS_INVALID"
    if (returncode==0 and len(rows)==2 and
        rows[0].get("starting") is True and
        rows[1].get("completed")==1 and
        rows[1].get("peer_token_verified") is True):
        return "CLEAN_SESSION"
    if returncode==3:
        error=rows[-1].get("error")
        if error in TRANSIENT_ERRORS and rows[0].get("starting") is True:
            return "TRANSIENT_PIPE_FAILURE"
        return "STARTUP_OR_SECURITY_DENIED"
    return "CHILD_PROCESS_CRASHED"


def run_fixed_child(args:list[str],*,max_seconds:int=10)->tuple[str,int|None]:
    """One process, bounded runtime and bounded output; NEVER shell=True."""
    if type(max_seconds) is not int or not 3<=max_seconds<=30:
        raise SupervisorError("SUPERVISOR_CHILD_DEADLINE_INVALID")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process=subprocess.Popen(
                args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                shell=False,close_fds=True)
        except OSError:
            return "CHILD_START_FAILED",None
        timed_out=False
        try:
            process.wait(timeout=max_seconds)
        except subprocess.TimeoutExpired:
            timed_out=True
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                return "CHILD_KILL_UNCONFIRMED",None
        stdout.seek(0)
        data=stdout.read(MAX_CHILD_OUTPUT+1)
        return classify(process.returncode,data,timed_out=timed_out),process.returncode


def _emit(record:dict):
    """Only fixed status fields; no credentials, paths, policy keys or payload."""
    print(json.dumps(record,ensure_ascii=True,sort_keys=True,
                     separators=(",",":")),flush=True)


def _command(ns)->list[str]:
    """Fixed executable + fixed script; no arbitrary child program argument."""
    command=[
        # -E prevents PYTHON* environment injection; -s removes the caller's
        # user site-packages before any broker imports can run. These flags
        # are not a substitute for protected Python executable/install ACLs.
        sys.executable,"-E","-s",
        str(Path(__file__).resolve().parent/"broker_named_pipe.py"),
        "--root",ns.root,"--policy",ns.policy,
        "--signed-policy-bundle",ns.signed_policy_bundle,
        "--trusted-issuer-public-hex",ns.trusted_issuer_public_hex,
        "--pipe-suffix",ns.pipe_suffix,"--broker-sid",ns.broker_sid,
        "--client-sid",ns.client_sid,
        "--max-connections","1",
    ]
    if ns.offline_same_sid_fixture:
        command.append("--offline-same-sid-fixture")
    return command


def run_sessions(ledger:AttemptLedger,command:list[str], *,
                 sessions:int,child_seconds:int,runner=None,
                 emit=None,now=None,sleep=None)->dict:
    """Purely bounded supervisor loop; test hooks do not appear in CLI.

    The runner is responsible for launching only the fixed child binary
    when called by main(). It receives NO previously executed request.
    """
    if type(sessions) is not int or not 1<=sessions<=MAX_SPAWNS:
        raise SupervisorError("SUPERVISOR_SESSIONS_INVALID")
    if type(child_seconds) is not int or not 3<=child_seconds<=30:
        raise SupervisorError("SUPERVISOR_CHILD_DEADLINE_INVALID")
    if type(command) is not list or not command or any(
            type(x) is not str for x in command):
        raise SupervisorError("SUPERVISOR_COMMAND_INVALID")
    runner=runner or run_fixed_child
    emit=emit or _emit
    now=now or (lambda:int(time.time()))
    sleep=sleep or time.sleep
    reason="NO_SESSION"
    started=0
    for index in range(sessions):
        try:
            attempt=ledger.acquire(now())
        except SupervisorError as exc:
            reason=str(exc)
            break
        started+=1
        emit({"event":"BROKER_LISTENER_STARTING",
              "session":index+1,"production_enforced":False,
              "mutating_command_replayed":False})
        try:
            reason,returncode=runner(command,max_seconds=child_seconds)
        except Exception:
            reason,returncode="CHILD_RUNNER_FAILED",None
        ledger.finish(attempt,reason,returncode)
        emit({"event":"BROKER_LISTENER_EXITED",
              "reason":reason,"session":index+1,
              "automatic_command_replay_permitted":False})
        if reason in ("CLEAN_SESSION","TRANSIENT_PIPE_FAILURE",
                      "CHILD_PROCESS_CRASHED","CHILD_WATCHDOG_TIMEOUT"):
            if reason!="CLEAN_SESSION" and index+1<sessions:
                sleep(min(2.0,0.2*(2**min(index,4))))
            continue
        break
    report=ledger.report()
    result={
        "event":"SUPERVISOR_STOPPED","last_reason":reason,
        "listeners_started_this_run":started,
        "durable_attempt_count":report["attempts"],
        "durable_pending_attempts":report["pending"],
        "automatic_command_replay_permitted":False,
        "live_mcp_modified":False,"production_enforced":False,
    }
    emit(result)
    return result


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description="Offline signed broker supervisor")
    parser.add_argument("--root",required=True)
    parser.add_argument("--policy",required=True)
    parser.add_argument("--signed-policy-bundle",required=True)
    parser.add_argument("--trusted-issuer-public-hex",required=True)
    parser.add_argument("--pipe-suffix",required=True)
    parser.add_argument("--broker-sid",required=True)
    parser.add_argument("--client-sid",required=True)
    parser.add_argument("--sessions",type=int,default=2)
    parser.add_argument("--max-attempts",type=int,default=5)
    parser.add_argument("--window-seconds",type=int,default=300)
    parser.add_argument("--child-seconds",type=int,default=9)
    parser.add_argument("--offline-same-sid-fixture",action="store_true")
    ns=parser.parse_args(argv)
    ledger=None
    try:
        root=_require_root(Path(ns.root))
        if (not 1<=ns.sessions<=MAX_SPAWNS or
            not 3<=ns.child_seconds<=30):
            raise SupervisorError("SUPERVISOR_LIMIT_INVALID")
        # Validate every primitive BEFORE spawning or writing attempt ledger.
        _name(ns.pipe_suffix)
        _validate_sid(ns.broker_sid,"BROKER_SID")
        _validate_sid(ns.client_sid,"CLIENT_SID")
        if current_user_sid()!=ns.broker_sid:
            raise SupervisorError("SUPERVISOR_PROCESS_SID_MISMATCH")
        if ns.offline_same_sid_fixture:
            if ns.client_sid!=ns.broker_sid:
                raise SupervisorError("SUPERVISOR_FIXTURE_SID_MISMATCH")
        elif ns.client_sid==ns.broker_sid:
            raise SupervisorError("SUPERVISOR_PRODUCTION_SAME_SID_DENIED")
        verified=verify_signed_policy(
            Path(ns.signed_policy_bundle),Path(ns.policy),
            ns.trusted_issuer_public_hex,datetime.now(timezone.utc))
        load_pinned_policy(Path(ns.policy),verified["policy_sha"])
        with single_instance(root):
            ledger=AttemptLedger(
                root,window_seconds=ns.window_seconds,
                max_window_attempts=ns.max_attempts)
            result=run_sessions(
                ledger,_command(ns),sessions=ns.sessions,
                child_seconds=ns.child_seconds)
            return 0 if result["last_reason"]=="CLEAN_SESSION" else 2
    except Exception:
        _emit({"event":"SUPERVISOR_DENIED","reason":"SUPERVISOR_PRECHECK_FAILED",
               "automatic_command_replay_permitted":False,
               "live_mcp_modified":False})
        return 3
    finally:
        if ledger is not None:
            ledger.close()


if __name__=="__main__":
    raise SystemExit(main())
