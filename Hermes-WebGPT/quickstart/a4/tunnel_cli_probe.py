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
    return {
        "found":True,
        "version_first_line":version.splitlines()[0][:85] if version else "unknown",
        "supports_runtimes_connect":"connect" in runtimes.lower(),
        "supports_runtime_api_key_env":"--runtime-api-key" in connect,
        "supports_mcp_server_url":"--mcp-server-url" in connect,
        "supports_alias":"--alias" in connect,
        "help_available":bool(top.strip()),
    }


if __name__=="__main__":
    try:
        print(json.dumps(inventory()))
    except (OSError, subprocess.TimeoutExpired):
        print(json.dumps({"found":False,"reason":"tunnel_inventory_unavailable"}))
