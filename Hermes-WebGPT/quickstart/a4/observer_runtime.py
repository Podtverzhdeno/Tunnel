"""Hermes A4: launch an *isolated* read-only native MCP HTTP endpoint.

Does not alter a running MCP, Tunnel, Relay, Telegram, or Yandex process.
The owner connects its authenticated endpoint to a dedicated tunnel only
after inspecting its configuration. Full-access mode deliberately refused.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from .observer_profile import ObserverConfigError, prepare
from .preflight import PreflightError, validate_settings

EXPECTED = frozenset({
    "list_workspaces", "list_directory", "directory_tree",
    "file_info", "read_file", "search_text",
})
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


class ObserverStartupError(RuntimeError):
    pass


# Hold process handles for the current Python command lifetime (relevant to
# local integration tests); never use this as authoritative cross-run state.
_LAUNCHED_CHILDREN: dict[int, subprocess.Popen] = {}


def locate_native_host(project: Path) -> Path:
    # In a local developer checkout, and in the published Tunnel repository.
    # Do not fetch or execute a remote binary implicitly.
    candidate = project.resolve(strict=True).parent / "Local-Workspace-Extensions" / "mcp-host-source"
    candidate = candidate.resolve(strict=False)
    if candidate.is_symlink() or not (candidate / "manage.py").is_file() or not (
        candidate / "workspace_mcp" / "tools.py"
    ).is_file():
        raise ObserverStartupError("trusted_native_host_not_installed")
    return candidate


def local_port_free(port: int) -> bool:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ObserverStartupError("invalid_observer_port")
    # Probe ownership by exclusive bind, not connect_ex: connect-only tests
    # can consume a pending connection on a listener with a small backlog.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _call(port: int, token: str, payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(f"http://127.0.0.1:{port}/mcp", method="POST", data=body,
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + token})
    with build_opener(ProxyHandler({})).open(request, timeout=1.5) as response:
        if response.status != 200:
            raise ObserverStartupError("unexpected_http_status")
        return json.load(response)


def audit_readonly(port: int, token: str, *, rpc=_call) -> dict:
    if not TOKEN_RE.fullmatch(token):
        raise ObserverStartupError("invalid_bearer")
    response = rpc(port, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = response.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise ObserverStartupError("native_tools_list_invalid")
    names = {x.get("name") for x in tools if isinstance(x, dict)}
    if names != EXPECTED or len(tools) != len(EXPECTED):
        raise ObserverStartupError("readonly_tool_inventory_mismatch")
    return {"ok": True, "read_only": True, "tool_count": len(names),
            "port": port, "tunnel_connected": False}


def _owned_observer_process(pid: int, port: int, config: Path) -> bool:
    """Verify a recorded PID by its Windows process command line, fail closed."""
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return False
    query = (
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = " + str(pid) +
        "' -ErrorAction Stop; if ($p) { $p.CommandLine }"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
            text=True, capture_output=True, timeout=8
        )
        line = completed.stdout.casefold()
        return (completed.returncode == 0 and "manage.py" in line
                and "--profile observer" in line
                and ("--port " + str(port)) in line
                and str(config).casefold() in line)
    except (OSError, subprocess.TimeoutExpired):
        return False


def start(*, project: Path, settings_path: Path, state: Path,
          port: int = 18766, host: Path | None = None,
          token: str | None = None) -> dict:
    """Fail closed on unverified occupied ports; never spawn duplicate host."""
    if not TOKEN_RE.fullmatch(token or ""):
        raise ObserverStartupError("missing_or_invalid_bearer")
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    validated = validate_settings(settings, project=project)
    if validated["mode"] != "read_only":
        raise ObserverStartupError("full_access_requires_trusted_broker")
    state = state.resolve(strict=True)
    if state.is_symlink() or state.is_relative_to(Path(validated["scope"])):
        raise ObserverStartupError("unsafe_observer_state")
    host = (host or locate_native_host(project)).resolve(strict=True)
    if not (host / "manage.py").is_file() or not (host / "workspace_mcp" / "http_server.py").is_file():
        raise ObserverStartupError("invalid_native_host")
    config_path = state / "a4-observer-config.json"
    receipt = state / "a4-observer-launch.json"
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise ObserverStartupError("unsafe_observer_receipt")
    if not local_port_free(port):
        if not receipt.is_file():
            raise ObserverStartupError("observer_port_occupied_manual_reconciliation")
        try:
            prior = json.loads(receipt.read_text(encoding="utf-8"))
            pid = prior["pid"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ObserverStartupError("invalid_observer_receipt")
        if (prior.get("port") != port or prior.get("profile") != "observer"
                or not _owned_observer_process(pid, port, config_path)):
            raise ObserverStartupError("observer_port_owned_by_unknown_process")
        return audit_readonly(port, token) | {"pid": pid, "reused": True}
    if receipt.exists():
        # The listener might be starting slowly or its PID reused. Never
        # overwrite or replay a prior start without reconciling ownership.
        raise ObserverStartupError("stale_observer_receipt_requires_reconciliation")
    prepare(settings, project, config_path)
    stdout = state / "a4-observer.stdout.log"
    stderr = state / "a4-observer.stderr.log"
    if stdout.is_symlink() or stderr.is_symlink():
        raise ObserverStartupError("unsafe_observer_logs")
    child_env = os.environ.copy()
    child_env["LOCAL_MCP_OBSERVER_BEARER"] = token
    child_env["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable, str(host / "manage.py"), "--config", str(config_path),
        "serve", "--port", str(port), "--profile", "observer",
    ]
    # Logs are not opened until configuration+permissions pass validation.
    with stdout.open("ab") as out, stderr.open("ab") as err:
        proc = subprocess.Popen(command, cwd=host, env=child_env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                close_fds=True, creationflags=(
                                    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                                ))
    _LAUNCHED_CHILDREN[proc.pid] = proc
    result = None
    try:
        for _ in range(36):
            if proc.poll() is not None:
                raise ObserverStartupError("observer_host_exited_before_ready")
            try:
                result = audit_readonly(port, token)
                break
            except (URLError, HTTPError, OSError, ValueError, ObserverStartupError):
                time.sleep(.25)
        if result is None:
            raise ObserverStartupError("observer_not_ready_or_not_readonly")
        # Receipt contains no bearer and does not imply cloud tunnel routing.
        receipt = state / "a4-observer-launch.json"
        if receipt.exists() or receipt.is_symlink():
            raise ObserverStartupError("observer_receipt_exists_manual_reconciliation")
        receipt.write_text(json.dumps({
            "pid": proc.pid, "port": port, "profile": "observer",
            "state": "LOCAL_READ_ONLY_READY_TUNNEL_NOT_CONNECTED"
        }), encoding="utf-8")
        return result | {"pid": proc.pid}
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("audit", "start"))
    parser.add_argument("--project", type=Path,
                        default=Path(__file__).resolve().parents[2])
    parser.add_argument("--settings", type=Path, required=False)
    parser.add_argument("--state", type=Path, required=False)
    parser.add_argument("--port", type=int, default=18766)
    opts = parser.parse_args(argv)
    try:
        bearer = os.environ.get("LOCAL_MCP_OBSERVER_BEARER", "")
        if opts.action == "audit":
            result = audit_readonly(opts.port, bearer)
        else:
            if opts.settings is None or opts.state is None:
                raise ObserverStartupError("missing_settings_or_state")
            result = start(project=opts.project, settings_path=opts.settings,
                           state=opts.state, port=opts.port, token=bearer)
        print(json.dumps(result))
        return 0
    except (ObserverStartupError, ObserverConfigError, PreflightError,
            OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Never emit raw paths, subprocess logs, or token values.
        print(json.dumps({"ok": False, "error": "observer_start_or_audit_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
