"""Personal, loopback-only bearer setup for the Local Workspace MCP observer.

Never prints bearer contents. Run from a clean Tunnel clone:
    python scripts/observer_auth.py init
    python scripts/observer_auth.py serve --port 8765

The bearer authenticates *only the read-only observer HTTP profile*.
It is not an OS-trusted owner/agent identity and never enables P085.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
STATE = ".local-mcp-state"
TOKEN_NAME = "observer-token"
HEADER_NAME = "observer-authorization-header"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "Local-Workspace-Extensions" / "mcp-host-source"


class AuthSetupError(RuntimeError):
    pass


def _paths(host_root: Path):
    host_root = host_root.expanduser()
    if host_root.is_symlink() or not host_root.is_dir():
        raise AuthSetupError("observer_host_root_missing_or_linked")
    if not (host_root / "manage.py").is_file():
        raise AuthSetupError("observer_manage_missing")
    folder = host_root / STATE
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise AuthSetupError("observer_state_invalid")
    return folder, folder / TOKEN_NAME, folder / HEADER_NAME


def _read_secret(path: Path):
    if path.is_symlink() or not path.is_file():
        raise AuthSetupError("observer_secret_missing_or_linked")
    try:
        if not 43 <= path.stat().st_size <= 150:
            raise AuthSetupError("observer_secret_size_invalid")
        value = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise AuthSetupError("observer_secret_unreadable") from exc
    return value


def _check(host_root: Path):
    folder, token_path, header_path = _paths(host_root)
    if folder.is_symlink() or not folder.is_dir():
        raise AuthSetupError("observer_state_missing")
    token = _read_secret(token_path)
    if TOKEN_RE.fullmatch(token) is None:
        raise AuthSetupError("observer_token_invalid")
    header = _read_secret(header_path)
    if header != "Bearer " + token:
        raise AuthSetupError("observer_header_mismatch")
    # Portable stat mode is meaningful on Unix. Windows uses inherited ACLs;
    # this is not a trusted OS service or elevated identity.
    if os.name != "nt" and (
        stat.S_IMODE(token_path.stat().st_mode) & 0o077 or
        stat.S_IMODE(header_path.stat().st_mode) & 0o077
    ):
        raise AuthSetupError("observer_secret_permissions_too_open")
    return token, header_path


def _create_one(path: Path, value: str):
    if path.is_symlink() or path.exists():
        raise AuthSetupError("observer_secret_already_exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(path, 0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def initialize(host_root: Path):
    folder, token_path, header_path = _paths(host_root)
    # Never silently rotate a secret while an existing tunnel may be using it.
    existing = token_path.exists() or header_path.exists() or token_path.is_symlink() or header_path.is_symlink()
    if existing:
        _check(host_root)
        return {"ok": True, "state": "already_initialized",
                "profile": "observer", "no_secret_printed": True}
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    key = secrets.token_urlsafe(48)
    _create_one(token_path, key)
    _create_one(header_path, "Bearer " + key)
    _check(host_root)
    return {"ok": True, "state": "created", "profile": "observer",
            "no_secret_printed": True}


def start(host_root: Path, port: int):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise AuthSetupError("observer_port_invalid")
    token, _ = _check(host_root)
    # Run the server in THIS process so stopping the launcher always stops
    # the actual HTTP server (on Windows, a subprocess child can outlive its
    # parent when Ctrl-C or Task Manager terminates the launcher).
    import runpy
    old_argv=sys.argv[:]
    old_path=sys.path[:]
    original=os.environ.get("LOCAL_MCP_OBSERVER_BEARER")
    os.environ["LOCAL_MCP_OBSERVER_BEARER"]=token
    sys.path.insert(0,str(host_root))
    sys.argv=[str(host_root/"manage.py"),"serve","--profile","observer","--port",str(port)]
    try:
        try:
            runpy.run_path(str(host_root/"manage.py"),run_name="__main__")
        except SystemExit as exc:
            return exc.code if type(exc.code) is int else 1
        return 0
    finally:
        sys.argv=old_argv
        sys.path[:]=old_path
        if original is None:
            os.environ.pop("LOCAL_MCP_OBSERVER_BEARER",None)
        else:
            os.environ["LOCAL_MCP_OBSERVER_BEARER"]=original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check", "serve"))
    parser.add_argument("--host-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            result = initialize(args.host_root)
        elif args.action == "check":
            _, header = _check(args.host_root)
            # The relative file name is enough to configure the tunnel.
            result = {"ok": True, "profile": "observer",
                      "state": "ready", "header_file": header.name,
                      "no_secret_printed": True}
        else:
            return start(args.host_root, args.port)
    except (AuthSetupError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
