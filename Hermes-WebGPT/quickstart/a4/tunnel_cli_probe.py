"""Read-only local tunnel binary capability inventory for A4.

No token, profile, runtime ID, private config or process arguments are read.
Only '--version' and '--help' are invoked; no tunnel is started or connected.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess

PROJECT=Path(__file__).resolve().parents[2]


def candidates():
    explicit=os.environ.get("HERMES_TUNNEL_EXE")
    if explicit:
        yield Path(explicit)
    yield PROJECT / "vendor" / "tunnel-client" / "tunnel-client.exe"
    yield Path.home() / "Downloads" / "tunnel-client-v0.0.19-windows-amd64" / "tunnel-client.exe"
    yield Path.home() / "Downloads" / "tunnel-client-v0.0.20-windows-amd64" / "tunnel-client.exe"
    resolved=shutil.which("tunnel-client.exe")
    if resolved:
        yield Path(resolved)


def clean_help(binary: Path, *args: str) -> str:
    p=subprocess.run([str(binary),*args],capture_output=True,timeout=8)
    # Never print raw output: CLI may accidentally echo environment data.
    return (p.stdout+p.stderr).decode("utf-8","replace") if p.returncode in (0,2) else ""


def inventory() -> dict:
    match=next((p for p in candidates() if p.is_file() and not p.is_symlink()),None)
    if match is None:
        return {"found":False,"supports_runtimes_connect":False,
                "supports_observer_headers":False}
    version=clean_help(match,"--version")
    top=clean_help(match,"--help")
    runtimes=clean_help(match,"runtimes","--help")
    connect=clean_help(match,"runtimes","connect","--help")
    listing=clean_help(match,"runtimes","list","--help")
    status=clean_help(match,"runtimes","status","--help")
    # Shape inspection only: never disclose runtime IDs, tokens or aliases.
    schema={"status":"unavailable"}
    if "--json" in listing:
        try:
            result=subprocess.run([str(match),"runtimes","list","--json"],
                                  capture_output=True,timeout=8)
            if result.returncode==0:
                document=json.loads(result.stdout)
                if isinstance(document,dict):
                    schema={"status":"ok","type":"object",
                            "keys":sorted(document.keys())[:20]}
                    aliases=document.get("aliases")
                    schema["aliases_shape"]=(
                        "mapping" if isinstance(aliases,dict) else
                        "array" if isinstance(aliases,list) else
                        type(aliases).__name__
                    )
                    schema["aliases_count"]=(
                        len(aliases) if isinstance(aliases,(dict,list)) else None)
                elif isinstance(document,list):
                    schema={"status":"ok","type":"array","count":len(document),
                            "item_keys":sorted(document[0].keys())[:20]
                            if document and isinstance(document[0],dict) else []}
                else:
                    schema={"status":"unexpected_type"}
            else:
                schema={"status":"command_error"}
        except (OSError,subprocess.TimeoutExpired,ValueError):
            schema={"status":"read_failed"}
    return {
        "found":True,
        "version_first_line":version.splitlines()[0][:85] if version else "unknown",
        "supports_runtimes_connect":"connect" in runtimes.lower(),
        "supports_runtime_api_key_env":"--runtime-api-key" in connect,
        "supports_mcp_server_url":"--mcp-server-url" in connect,
        "supports_alias":"--alias" in connect,
        "supports_list_json":bool(listing.strip()) and "--json" in listing,
        "supports_status_json":bool(status.strip()) and "--json" in status,
        "runtimes_list_in_help":"list" in runtimes.lower(),
        "list_schema":schema,
        "help_available":bool(top.strip()),
    }


if __name__=="__main__":
    try:
        print(json.dumps(inventory()))
    except (OSError, subprocess.TimeoutExpired):
        print(json.dumps({"found":False,"reason":"tunnel_inventory_unavailable"}))
