"""Offline stdio broker process for the experimental foundation kernel.

Security boundary is an OS process *only if* the launcher and data directory
are isolated by OS permissions and the parent does NOT retain direct write or
run_command access. This implementation deliberately does not modify MCP.
Only two signed operations are exposed; no shell, network, Git, PLAN/LEASE,
worker invocation, owner approval or deployment.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

try:
    from .journal import Journal
    from .atomic_dispatch import AtomicDispatch
    from .host_auth import HostGrant, HostPolicy
    from .host_gate import HostDispatchGate
    from .transition import validate_id
except ImportError:
    from journal import Journal
    from atomic_dispatch import AtomicDispatch
    from host_auth import HostGrant, HostPolicy
    from host_gate import HostDispatchGate
    from transition import validate_id

PROTOCOL = "foundation-broker-stdio-v1"
POLICY_SCHEMA = "foundation-pinned-policy-v1"
MAX_CONFIG = 32768
MAX_FRAME = 16384
MAX_FRAMES = 512
GRANT_KEYS = frozenset({
    "principal_id", "role", "key_id", "public_key_b64", "scope",
    "run_id", "step_id", "epoch", "policy_revision", "capabilities",
    "revoked",
})
COMMON = frozenset({"protocol", "op"})
MUTATE = frozenset({"auth", "run_id", "step_id", "request_id", "epoch"})
STAGE = COMMON | MUTATE | frozenset({"message_id", "destination", "payload_sha"})
MARK = COMMON | MUTATE | frozenset({"message_id", "attempt_id"})
QUERY = COMMON | MUTATE | frozenset({"message_id", "target_request_id"})
KNOWN = frozenset({"health", "stage_intent", "mark_send_uncertain",
                   "query_command_status"})


class BrokerError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrokerError("DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _strict_json(data: bytes):
    try:
        text = data.decode("utf-8")
        return json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                BrokerError("NONFINITE_JSON_NUMBER")))
    except UnicodeError as exc:
        raise BrokerError("INVALID_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise BrokerError("INVALID_JSON") from exc


def _file_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink() or getattr(path, "is_junction", lambda:False)():
        raise BrokerError("PINNED_POLICY_LINK_DENIED")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BrokerError("PINNED_POLICY_UNAVAILABLE") from exc
    if not path.is_file() or size < 2 or size > limit:
        raise BrokerError("PINNED_POLICY_INVALID_SIZE")
    raw = path.read_bytes()
    if len(raw) != size:
        raise BrokerError("PINNED_POLICY_CHANGED_DURING_READ")
    return raw


def load_pinned_policy(path: Path, pinned_sha: str) -> tuple[str,HostPolicy]:
    if (type(pinned_sha) is not str or len(pinned_sha)!=64 or
        any(c not in "0123456789abcdef" for c in pinned_sha)):
        raise BrokerError("EXPECTED_POLICY_SHA_INVALID")
    raw=_file_bytes(path,MAX_CONFIG)
    if hashlib.sha256(raw).hexdigest()!=pinned_sha:
        raise BrokerError("PINNED_POLICY_SHA_MISMATCH")
    doc=_strict_json(raw)
    if type(doc) is not dict or set(doc)!={"schema","scope","grants"}:
        raise BrokerError("PINNED_POLICY_SCHEMA_INVALID")
    if doc["schema"]!=POLICY_SCHEMA:
        raise BrokerError("PINNED_POLICY_VERSION_DENIED")
    try:
        validate_id(doc["scope"],"POLICY_SCOPE")
    except ValueError as exc:
        raise BrokerError("PINNED_POLICY_SCOPE_INVALID") from exc
    raw_grants=doc["grants"]
    if type(raw_grants) is not list or not 1<=len(raw_grants)<=64:
        raise BrokerError("PINNED_POLICY_GRANTS_INVALID")
    grants=[]
    for item in raw_grants:
        if type(item) is not dict or set(item)!=GRANT_KEYS:
            raise BrokerError("PINNED_GRANT_FIELDS_INVALID")
        if item["scope"]!=doc["scope"]:
            raise BrokerError("PINNED_GRANT_SCOPE_INVALID")
        value=item["public_key_b64"]
        if type(value) is not str or len(value)>64:
            raise BrokerError("PINNED_GRANT_PUBLIC_KEY_INVALID")
        try:
            decoded=base64.b64decode(value,validate=True)
        except (ValueError,binascii.Error) as exc:
            raise BrokerError("PINNED_GRANT_PUBLIC_KEY_INVALID") from exc
        if len(decoded)!=32 or base64.b64encode(decoded).decode("ascii")!=value:
            raise BrokerError("PINNED_GRANT_PUBLIC_KEY_INVALID")
        if type(item["capabilities"]) is not list or any(
            type(x) is not str for x in item["capabilities"]
        ):
            raise BrokerError("PINNED_GRANT_CAPABILITIES_INVALID")
        if len(set(item["capabilities"]))!=len(item["capabilities"]):
            raise BrokerError("PINNED_GRANT_DUPLICATE_CAPABILITY")
        grants.append(HostGrant(
            principal_id=item["principal_id"],role=item["role"],
            key_id=item["key_id"],public_key=decoded,
            scope=item["scope"],run_id=item["run_id"],
            step_id=item["step_id"],epoch=item["epoch"],
            policy_revision=item["policy_revision"],
            capabilities=frozenset(item["capabilities"]),
            revoked=item["revoked"],
        ))
    return doc["scope"],HostPolicy(tuple(grants))


class StdioBroker:
    def __init__(self, root: Path, policy: HostPolicy, scope: str):
        # Journal constructor rejects links and non-existing root.
        self.journal=Journal(root,"execution_journal_broker.sqlite3")
        try:
            self.dispatch=AtomicDispatch(self.journal)
            self.gate=HostDispatchGate(self.dispatch,policy)
        except BaseException:
            self.journal.close()
            raise
        self.scope=scope

    def close(self):
        self.journal.close()

    def handle(self, request: object) -> dict:
        if type(request) is not dict or request.get("protocol")!=PROTOCOL:
            raise BrokerError("BROKER_REQUEST_SCHEMA_INVALID")
        op=request.get("op")
        if type(op) is not str or op not in KNOWN:
            raise BrokerError("BROKER_OPERATION_DENIED")
        if op=="health":
            if set(request)!=COMMON:
                raise BrokerError("BROKER_FIELDS_INVALID")
            return {
                "ok":True,"protocol":PROTOCOL,"ready":True,
                "mode":"OFFLINE_ISOLATED_BROKER",
                "mcp_connected":False,"external_effects_performed":False,
                "unrestricted_tool_access_removed":False,
            }
        expected = (STAGE if op=="stage_intent" else
                    MARK if op=="mark_send_uncertain" else QUERY)
        if set(request)!=expected:
            raise BrokerError("BROKER_FIELDS_INVALID")
        auth=request["auth"]
        if type(auth) is not dict:
            raise BrokerError("BROKER_AUTH_INVALID")
        context=dict(
            now=datetime.now(timezone.utc),scope=self.scope,
            run_id=request["run_id"],step_id=request["step_id"],
            request_id=request["request_id"],epoch=request["epoch"],
        )
        if op=="stage_intent":
            result=self.gate.stage_intent(
                auth,**context,message_id=request["message_id"],
                destination=request["destination"],
                payload_sha=request["payload_sha"])
        elif op=="mark_send_uncertain":
            result=self.gate.mark_send_uncertain(
                auth,**context,message_id=request["message_id"],
                attempt_id=request["attempt_id"])
        else:
            try:
                from .broker_reconcile import reconcile_command
            except ImportError:
                from broker_reconcile import reconcile_command
            result=reconcile_command(
                self.gate,envelope=auth,**context,
                message_id=request["message_id"],
                target_request_id=request["target_request_id"])
        return {"ok":True,"protocol":PROTOCOL,
                "result":result, "mcp_connected":False}


def process_input(broker: StdioBroker, inp, out, *, max_frames: int=MAX_FRAMES) -> int:
    for _ in range(max_frames):
        frame=inp.readline(MAX_FRAME+1)
        if not frame:
            return 0
        if len(frame)>MAX_FRAME or not frame.endswith(b"\n"):
            _respond(out,{"ok":False,"error":"BROKER_FRAME_TOO_LARGE_OR_UNTERMINATED"})
            # Terminal protocol error: never try to resync an ambiguous frame.
            return 2
        try:
            parsed=_strict_json(frame)
            response=broker.handle(parsed)
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            text=str(exc)
            # Only deliberately named codes are surfaced, never raw SQL or paths.
            code=text if (
                len(text)<=90 and text and all(
                    x in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for x in text)
            ) else "BROKER_REQUEST_DENIED"
            response={"ok":False,"error":code}
        except Exception:
            response={"ok":False,"error":"BROKER_INTERNAL_FAILURE"}
        _respond(out,response)
    _respond(out,{"ok":False,"error":"BROKER_FRAME_QUOTA_EXCEEDED"})
    return 2


def _respond(out, response: dict):
    raw=json.dumps(response,sort_keys=True,separators=(",",":"),
                   ensure_ascii=True,allow_nan=False).encode("utf-8")
    out.write(raw+b"\n")
    out.flush()


def main(argv=None, *, inp=None, out=None) -> int:
    parser=argparse.ArgumentParser(description="Isolated broker test harness")
    parser.add_argument("--root",required=True)
    parser.add_argument("--policy",required=True)
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--signed-policy-bundle")
    parser.add_argument("--trusted-issuer-public-hex")
    parser.add_argument("--legacy-unsigned-policy-for-tests",action="store_true")
    args=parser.parse_args(argv)
    inp = inp if inp is not None else sys.stdin.buffer
    out = out if out is not None else sys.stdout.buffer
    broker=None
    try:
        signed_mode=not args.legacy_unsigned_policy_for_tests
        if signed_mode:
            if not args.signed_policy_bundle or not args.trusted_issuer_public_hex:
                raise BrokerError("SIGNED_POLICY_REQUIRED")
            try:
                from .signed_policy import verify_signed_policy, SignedPolicyFloor
            except ImportError:
                from signed_policy import verify_signed_policy, SignedPolicyFloor
            verified=verify_signed_policy(
                Path(args.signed_policy_bundle),Path(args.policy),
                args.trusted_issuer_public_hex,datetime.now(timezone.utc))
            if (args.expected_policy_sha256 is not None and
                args.expected_policy_sha256!=verified["policy_sha"]):
                raise BrokerError("SIGNED_POLICY_EXTRA_PIN_MISMATCH")
            scope,policy=load_pinned_policy(
                Path(args.policy),verified["policy_sha"])
            broker=StdioBroker(Path(args.root),policy,scope)
            # Protected OS identity + protected DB are required for real trust.
            floor=SignedPolicyFloor(broker.journal.db)
            broker.gate.active_policy_floor=floor.admit(verified)
            broker.gate.active_policy_expiry_utc=datetime.strptime(
                verified["expires_utc"],"%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        else:
            if (not args.expected_policy_sha256 or args.signed_policy_bundle or
                args.trusted_issuer_public_hex):
                raise BrokerError("LEGACY_FIXTURE_FLAGS_INVALID")
            scope,policy=load_pinned_policy(
                Path(args.policy),args.expected_policy_sha256)
            broker=StdioBroker(Path(args.root),policy,scope)
    except Exception:
        if broker is not None:
            broker.close()
        _respond(out,{"ok":False,"error":"BROKER_STARTUP_DENIED"})
        return 3
    try:
        return process_input(broker,inp,out)
    finally:
        broker.close()


if __name__=="__main__":
    raise SystemExit(main())
