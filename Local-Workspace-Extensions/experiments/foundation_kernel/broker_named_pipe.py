"""Standalone signed-policy broker over a Win32 SID-authenticated pipe.

Never installed in the live MCP App. Requires separate broker/client SIDs,
conservative OS file ACL preflight and an issuer-signed, monotonic grant policy
by default. --offline-same-sid-fixture exists ONLY for disposable tests;
the resulting process reports NO OS isolation.

Pipe peer SID is taken from the Win32 impersonation token, not request JSON.
There is no shell, Git, network, independent worker execution or approval.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime,timezone
from pathlib import Path

try:
    from .broker_stdio import StdioBroker,load_pinned_policy,_strict_json
    from .signed_policy import verify_signed_policy,SignedPolicyFloor
    from .win_pipe_ipc import serve_one,PipeAccessError
    from .windows_isolation import current_user_sid,preflight_isolation
except ImportError:
    from broker_stdio import StdioBroker,load_pinned_policy,_strict_json
    from signed_policy import verify_signed_policy,SignedPolicyFloor
    from win_pipe_ipc import serve_one,PipeAccessError
    from windows_isolation import current_user_sid,preflight_isolation

PROTOCOL="win32-signed-broker-v1"
MAX_CONNECTIONS=32

class NativeBrokerError(ValueError):
    pass

def sanitize(exc: Exception) -> str:
    code=str(exc)
    if (code and len(code)<=90 and all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in code)):
        return code
    return "SIGNED_BROKER_REQUEST_DENIED"

def handle_frame(broker: StdioBroker, raw: bytes) -> bytes:
    """Transform verified-SID pipe data into one signed broker response."""
    try:
        req=_strict_json(raw)
        if type(req) is not dict:
            raise NativeBrokerError("BROKER_REQUEST_SCHEMA_INVALID")
        result=broker.handle(req)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result={"ok":False,"error":sanitize(exc)}
    except Exception:
        result={"ok":False,"error":"SIGNED_BROKER_INTERNAL_ERROR"}
    return json.dumps(
        result,ensure_ascii=True,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode("utf-8")

def open_signed_broker(root: Path, policy_path: Path,bundle: Path,
                       issuer_hex: str) -> StdioBroker:
    verified=verify_signed_policy(
        bundle,policy_path,issuer_hex,datetime.now(timezone.utc))
    scope,grants=load_pinned_policy(policy_path,verified["policy_sha"])
    broker=StdioBroker(root,grants,scope)
    try:
        floor=SignedPolicyFloor(broker.journal.db)
        broker.gate.active_policy_floor=floor.admit(verified)
        broker.gate.active_policy_expiry_utc=datetime.strptime(
            verified["expires_utc"],"%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except Exception:
        broker.close()
        raise
    return broker

def trusted_python_startup() -> bool:
    """Verify flags selected by an independent, protected parent launcher."""
    return bool(sys.flags.ignore_environment and sys.flags.no_user_site)


def serve(argv=None, *, out=None):
    parser=argparse.ArgumentParser(
        description="Experimental protected Windows named pipe broker")
    parser.add_argument("--root",required=True)
    parser.add_argument("--policy",required=True)
    parser.add_argument("--signed-policy-bundle",required=True)
    parser.add_argument("--trusted-issuer-public-hex",required=True)
    parser.add_argument("--pipe-suffix",required=True)
    parser.add_argument("--broker-sid",required=True)
    parser.add_argument("--client-sid",required=True)
    parser.add_argument("--max-connections",type=int,default=1)
    parser.add_argument("--offline-same-sid-fixture",action="store_true")
    args=parser.parse_args(argv)
    out=out or sys.stdout
    broker=None
    mode="ISOLATION_NOT_VERIFIED"
    try:
        if not 1<=args.max_connections<=MAX_CONNECTIONS:
            raise NativeBrokerError("PIPE_CONNECTION_QUOTA_INVALID")
        if args.broker_sid!=current_user_sid():
            raise NativeBrokerError("PIPE_BROKER_PROCESS_TOKEN_MISMATCH")
        if args.offline_same_sid_fixture:
            if args.client_sid!=args.broker_sid:
                raise NativeBrokerError("OFFLINE_FIXTURE_MUST_USE_SAME_SID")
            mode="SAME_SID_TEST_ONLY"
        else:
            # Startup flags must be enforced by the OS-protected launcher,
            # before this module imports any dependencies. A regular Python
            # startup accepts PYTHONPATH / user site injection before the
            # broker can inspect ACLs. -E -s are minimum requirements.
            if not trusted_python_startup():
                raise NativeBrokerError("PIPE_PYTHON_STARTUP_NOT_ISOLATED")
            if args.client_sid==args.broker_sid:
                raise NativeBrokerError("PIPE_SAME_SID_PRODUCTION_DENIED")
            # An OS-protected launcher, executable, configuration and
            # state are prerequisites; never rely on a caller-provided SID
            # without reading the actual server/peer Windows tokens.
            # Never protect only the entrypoint: a writable Python import
            # in the same directory is equally privileged as the launcher.
            # Cover all shipped non-test Python modules and the interpreter.
            code_dir=Path(__file__).resolve().parent
            if code_dir.is_symlink() or getattr(
                    code_dir,"is_junction",lambda:False)():
                raise NativeBrokerError("PIPE_CODE_DIRECTORY_LINK_DENIED")
            immutable=[Path(sys.executable).resolve(strict=True)]
            for file in sorted(code_dir.iterdir()):
                if file.suffix!=".py" or file.name.startswith("test_"):
                    continue
                if (file.is_symlink() or
                    getattr(file,"is_junction",lambda:False)() or
                    not file.is_file()):
                    raise NativeBrokerError("PIPE_CODE_MODULE_INVALID")
                immutable.append(file.resolve(strict=True))
            if not 7<=len(immutable)<=60:
                raise NativeBrokerError("PIPE_CODE_MODULE_INVENTORY_INVALID")
            required=[
                Path(args.root),
                Path(args.root)/"execution_journal_broker.sqlite3",
                Path(args.policy),Path(args.signed_policy_bundle),
                *immutable,
            ]
            inspect=preflight_isolation(
                broker_sid=args.broker_sid,
                agent_sid=args.client_sid,
                protected_paths=required,
                immutable_code_paths=immutable)
            if not inspect["filesystem_preflight_passed"]:
                raise NativeBrokerError("PIPE_PROTECTED_HOST_PREFLIGHT_FAILED")
            mode="ACL_AND_PEER_SID_PREFLIGHT_ONLY"
        broker=open_signed_broker(
            Path(args.root),Path(args.policy),
            Path(args.signed_policy_bundle),
            args.trusted_issuer_public_hex)
        print(json.dumps(
            {"ok":True,"starting":True,"mode":mode,
             "os_isolated":False,"mcp_registered":False},
            sort_keys=True),file=out,flush=True)
        observed=[]
        for _ in range(args.max_connections):
            actual=serve_one(
                suffix=args.pipe_suffix,broker_sid=args.broker_sid,
                client_sid=args.client_sid,
                handler=lambda data:handle_frame(broker,data))
            observed.append(actual["os_peer_token_checked"])
        print(json.dumps({"ok":True,"completed":len(observed),
                          "peer_token_verified":all(observed),
                          "mode":mode,"live_host_modified":False,
                          "production_mcp_enforced":False},
                         sort_keys=True),file=out,flush=True)
        return 0
    except PipeAccessError as exc:
        # Only fixed transport failure codes are safe for a supervisor to
        # classify. A restart opens a listener ONLY; it never resends work.
        code=str(exc)
        restartable={
            "PIPE_CONNECT_TIMEOUT","PIPE_READ_TIMEOUT",
            "PIPE_READ_FAILED","PIPE_SEND_FAILED","PIPE_WRITE_TIMEOUT",
            "PIPE_CONNECT_FAILED","PIPE_CLIENT_CONNECTION_DENIED",
        }
        if code not in restartable:
            code="SIGNED_NAMED_PIPE_BROKER_DENIED"
        print(json.dumps(
            {"ok":False,"error":code,
             "mode":mode,"live_host_modified":False},
            sort_keys=True),file=out,flush=True)
        return 3
    except Exception:
        print(json.dumps(
            {"ok":False,"error":"SIGNED_NAMED_PIPE_BROKER_DENIED",
             "mode":mode,"live_host_modified":False},
            sort_keys=True),file=out,flush=True)
        return 3
    finally:
        if broker is not None:
            broker.close()

if __name__=="__main__":
    raise SystemExit(serve())
