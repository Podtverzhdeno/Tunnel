"""Experimental standalone MCP stdio front-end for the isolated broker.

It is NOT installed in the connected Local Workspace MCP. Each supported
tools/call starts a new broker child using argv (shell=False), passes exactly
one bounded JSON request, and returns a bounded result. The only mutating
tools are signed STAGE_INTENT and MARK_SEND_UNCERTAIN. No file/command/Git
tools, approval, deploy, live queue or network transport exist here.

OS security caveat: child runs as the *same user* unless a future host launcher
uses a restricted OS identity and grants. A same-user caller with access to
state files can bypass this prototype. This is a testable IPC architecture,
NOT production isolation or a successful MCP plugin installation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from .broker_stdio import PROTOCOL, _strict_json
except ImportError:
    from broker_stdio import PROTOCOL, _strict_json

MCP_VERSION = "2025-11-25"
MAX_MCP_FRAME = 65536
MAX_CHILD_OUTPUT = 16384
MAX_REQUESTS = 512

_BASE_FIELDS = {
    "auth":{"type":"object",
            "required":["document","signature_b64"],
            "additionalProperties":False,
            "properties":{"document":{"type":"object"},
                          "signature_b64":{"type":"string"}}},
    "run_id":{"type":"string","minLength":1,"maxLength":96},
    "step_id":{"type":"string","minLength":1,"maxLength":96},
    "request_id":{"type":"string","minLength":1,"maxLength":96},
    "epoch":{"type":"integer","minimum":1},
    "message_id":{"type":"string","minLength":1,"maxLength":96},
}

def _tool(name, description, fields):
    prop={k:_BASE_FIELDS[k] for k in
          ("auth","run_id","step_id","request_id","epoch","message_id")}
    prop.update(fields)
    return {"name":name, "description":description,
            "inputSchema":{
                "type":"object","properties":prop,
                "required":list(prop),"additionalProperties":False}}

TOOLS=[
    {"name":"broker_health",
     "description":"Health of standalone offline signed broker; NOT the live MCP host",
     "inputSchema":{"type":"object","properties":{},
                    "required":[],"additionalProperties":False}},
    _tool("broker_stage_intent",
          "Stage signed worker message + journal intent in one local transaction",
          {"destination":{"type":"string","minLength":1,"maxLength":96},
           "payload_sha":{"type":"string","pattern":"^[a-f0-9]{64}$"}}),
    _tool("broker_mark_send_uncertain",
          "Signed, one-way pre-send fence; does not send any network message",
          {"attempt_id":{"type":"string","minLength":1,"maxLength":96}}),
    _tool("broker_query_command_status",
          "Signed read-only lost-reply reconciliation; NEVER retries effects",
          {"target_request_id":{"type":"string","minLength":1,"maxLength":96}}),
]
_TOOL_BY_NAME={tool["name"]:tool for tool in TOOLS}
_OPERATIONS={
    "broker_health":"health",
    "broker_stage_intent":"stage_intent",
    "broker_mark_send_uncertain":"mark_send_uncertain",
    "broker_query_command_status":"query_command_status",
}

class BridgeError(ValueError):
    pass

class BrokerProxy:
    def __init__(self, root: str, policy: str, expected_sha: str | None,
                 *, signed_policy_bundle: str | None = None,
                 trusted_issuer_public_hex: str | None = None,
                 legacy_unsigned_policy_for_tests: bool = False):
        self.command=[
            sys.executable,
            str(Path(__file__).resolve().parent/"broker_stdio.py"),
            "--root",root,"--policy",policy,
        ]
        if expected_sha:
            self.command.extend(["--expected-policy-sha256",expected_sha])
        if signed_policy_bundle:
            self.command.extend(["--signed-policy-bundle",signed_policy_bundle])
        if trusted_issuer_public_hex:
            self.command.extend(["--trusted-issuer-public-hex",trusted_issuer_public_hex])
        if legacy_unsigned_policy_for_tests:
            self.command.append("--legacy-unsigned-policy-for-tests")

    def call(self, request: dict) -> dict:
        raw=json.dumps(request,sort_keys=True,separators=(",",":"),
                       ensure_ascii=True,allow_nan=False).encode("utf-8")+b"\n"
        if len(raw)>16384:
            return {"ok":False,"error":"BRIDGE_REQUEST_TOO_LARGE"}
        try:
            run=subprocess.run(
                self.command,input=raw,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,check=False,
                timeout=8,shell=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"ok":False,"error":"BROKER_PROCESS_UNAVAILABLE"}
        if len(run.stdout)>MAX_CHILD_OUTPUT or len(run.stdout.splitlines())!=1:
            return {"ok":False,"error":"BROKER_INVALID_REPLY"}
        try:
            reply=_strict_json(run.stdout.strip())
        except (ValueError,TypeError):
            return {"ok":False,"error":"BROKER_INVALID_REPLY"}
        if type(reply) is not dict or type(reply.get("ok")) is not bool:
            return {"ok":False,"error":"BROKER_INVALID_REPLY"}
        if run.returncode not in (0,2,3):
            return {"ok":False,"error":"BROKER_PROCESS_FAILED"}
        return reply


def _error(code, message, ident):
    return {"jsonrpc":"2.0","id":ident,
            "error":{"code":code,"message":message}}


def _arguments(name, args):
    if type(args) is not dict:
        raise BridgeError("MCP_ARGUMENTS_INVALID")
    expected=set(_TOOL_BY_NAME[name]["inputSchema"]["required"])
    if set(args)!=expected:
        raise BridgeError("MCP_ARGUMENTS_INVALID")
    return args


def handle_mcp(req: dict, proxy: BrokerProxy):
    if type(req) is not dict or req.get("jsonrpc")!="2.0":
        return _error(-32600,"Invalid Request",None)
    ident=req.get("id")
    if not (ident is None or type(ident) is str or type(ident) is int):
        return _error(-32600,"Invalid Request",None)
    method=req.get("method")
    if type(method) is not str:
        return _error(-32600,"Invalid Request",ident)
    if method=="notifications/initialized":
        return None
    if method=="initialize":
        return {"jsonrpc":"2.0","id":ident,
                "result":{"protocolVersion":MCP_VERSION,
                          "capabilities":{"tools":{}},
                          "serverInfo":{"name":"isolated-broker-experimental",
                                        "version":"0.1.0"}}}
    if method=="ping":
        return {"jsonrpc":"2.0","id":ident,"result":{}}
    if method=="tools/list":
        return {"jsonrpc":"2.0","id":ident,"result":{"tools":TOOLS}}
    if method!="tools/call":
        return _error(-32601,"Method not found",ident)
    params=req.get("params")
    if type(params) is not dict or set(params)!={"name","arguments"}:
        return _error(-32602,"Invalid params",ident)
    name=params["name"]
    if type(name) is not str or name not in _OPERATIONS:
        return _error(-32602,"Tool not exposed",ident)
    try:
        args=_arguments(name,params["arguments"])
    except BridgeError:
        return _error(-32602,"Invalid params",ident)
    broker_request={"protocol":PROTOCOL,"op":_OPERATIONS[name],**args}
    reply=proxy.call(broker_request)
    return {"jsonrpc":"2.0","id":ident,
            "result":{"content":[{"type":"text",
                                  "text":json.dumps(reply,sort_keys=True,
                                           ensure_ascii=True,
                                           separators=(",",":"))}],
                      "isError":not reply["ok"]}}


def run_stdio(inp,out,proxy,*,limit=MAX_REQUESTS):
    for _ in range(limit):
        line=inp.readline(MAX_MCP_FRAME+1)
        if not line:
            return 0
        if len(line)>MAX_MCP_FRAME or not line.endswith(b"\n"):
            send(out,_error(-32700,"Frame too large or incomplete",None))
            return 2
        try:
            obj=_strict_json(line)
        except (ValueError,TypeError):
            send(out,_error(-32700,"Parse error",None))
            continue
        try:
            response=handle_mcp(obj,proxy)
        except Exception:
            response=_error(-32603,"Internal error",obj.get("id") if type(obj) is dict else None)
        if response is not None:
            send(out,response)
    send(out,_error(-32600,"Message quota exceeded",None))
    return 2


def send(out,reply):
    raw=json.dumps(reply,ensure_ascii=True,sort_keys=True,
                   separators=(",",":"),allow_nan=False).encode("utf-8")
    out.write(raw+b"\n")
    out.flush()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline restricted MCP stdio adapter")
    parser.add_argument("--root",required=True)
    parser.add_argument("--policy",required=True)
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--signed-policy-bundle")
    parser.add_argument("--trusted-issuer-public-hex")
    parser.add_argument("--legacy-unsigned-policy-for-tests",action="store_true")
    args=parser.parse_args(argv)
    proxy=BrokerProxy(
        args.root,args.policy,args.expected_policy_sha256,
        signed_policy_bundle=args.signed_policy_bundle,
        trusted_issuer_public_hex=args.trusted_issuer_public_hex,
        legacy_unsigned_policy_for_tests=args.legacy_unsigned_policy_for_tests)
    return run_stdio(sys.stdin.buffer,sys.stdout.buffer,proxy)


if __name__=="__main__":
    raise SystemExit(main())
