"""Standalone MCP stdio -> verified Windows Named Pipe -> signed broker.

NOT installed in the connected Local Workspace MCP. The pipe client pins
the actual server PROCESS token SID before sending any data; the broker
independently impersonates and verifies the client SID after reading.
Commands remain Ed25519-signed and capability scoped by the broker.

Critical: the pipe suffix and expected server SID must be injected by a
trusted launcher. This adapter does not protect launch arguments or grant
any new privileges to an arbitrary agent or the running MCP host.
"""
from __future__ import annotations
import argparse
import json
import sys

try:
    from .broker_mcp_bridge import run_stdio
    from .win_pipe_ipc import MAX_FRAME,request_once,PipeAccessError
    from .broker_stdio import _strict_json, PROTOCOL
except ImportError:
    from broker_mcp_bridge import run_stdio
    from win_pipe_ipc import MAX_FRAME,request_once,PipeAccessError
    from broker_stdio import _strict_json, PROTOCOL

class NamedPipeProxy:
    def __init__(self, suffix: str, expected_server_sid: str):
        self.suffix=suffix
        self.expected_server_sid=expected_server_sid

    def call(self, request: dict) -> dict:
        try:
            raw=json.dumps(
                request,ensure_ascii=True,sort_keys=True,
                separators=(",",":"),allow_nan=False).encode("utf-8")
            if len(raw)>MAX_FRAME:
                return {"ok":False,"error":"PIPE_MCP_REQUEST_TOO_LARGE"}
            response=request_once(
                self.suffix,raw,expected_server_sid=self.expected_server_sid)
            doc=_strict_json(response)
            if type(doc) is not dict or type(doc.get("ok")) is not bool:
                return {"ok":False,"error":"PIPE_MCP_REPLY_INVALID"}
            return doc
        except (PipeAccessError,ValueError,TypeError,OverflowError):
            return {"ok":False,"error":"PIPE_MCP_PEER_OR_PROTOCOL_DENIED"}
        except Exception:
            return {"ok":False,"error":"PIPE_MCP_UNEXPECTED_FAILURE"}

def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Experimental verified Windows pipe MCP adapter")
    parser.add_argument("--pipe-suffix",required=True)
    parser.add_argument("--expected-server-sid",required=True)
    args=parser.parse_args(argv)
    proxy=NamedPipeProxy(args.pipe_suffix,args.expected_server_sid)
    return run_stdio(sys.stdin.buffer,sys.stdout.buffer,proxy)

if __name__=="__main__":
    raise SystemExit(main())
