"""A4 dedicated read-only observer Tunnel onboarding.

Only an explicit owner-approved connect() call creates an external binding.
No current production alias is modified. A durable attempt record prevents
automatic replay after ambiguous network errors.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .observer_runtime import audit_readonly, ObserverStartupError
from .tunnel_cli_probe import candidates

ALIAS = "hermes-a4-observer"
TUNNEL_ID_RE = re.compile(r"^tunnel_[a-zA-Z0-9_-]{8,128}$")
PORT = 18766


class TunnelConnectError(RuntimeError):
    pass


def find_cli() -> Path:
    for candidate in candidates():
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raise TunnelConnectError("tunnel_client_not_installed")


def decode_aliases(document: object) -> frozenset[str]:
    """Match tunnel-client 0.0.19 JSON alias schema; reject unknown variants."""
    if not isinstance(document, dict) or not isinstance(document.get("aliases"), list):
        raise TunnelConnectError("unrecognized_tunnel_list_schema")
    names = []
    for item in document["aliases"]:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict) and type(item.get("alias")) is str:
            names.append(item["alias"])
        elif isinstance(item, dict) and type(item.get("name")) is str:
            names.append(item["name"])
        else:
            raise TunnelConnectError("unrecognized_tunnel_alias_entry")
    return frozenset(names)


def get_aliases(cli: Path) -> frozenset[str]:
    """Never print raw CLI output: it may include account/runtime metadata."""
    try:
        result = subprocess.run([str(cli), "runtimes", "list", "--json"],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise TunnelConnectError("tunnel_alias_inventory_failed")
        return decode_aliases(json.loads(result.stdout))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise TunnelConnectError("tunnel_alias_inventory_failed") from exc


def runtime_status(cli: Path, *, env: dict[str, str] | None = None) -> dict:
    """Verify the managed local runtime is alive; no remote E2E inference."""
    try:
        result = subprocess.run(
            [str(cli), "runtimes", "status", ALIAS, "--json"],
            env=env, capture_output=True, timeout=15)
        if result.returncode != 0:
            raise TunnelConnectError("tunnel_status_command_failed")
        payload = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise TunnelConnectError("tunnel_status_command_failed") from exc
    if not isinstance(payload, dict) or payload.get("alias") != ALIAS:
        raise TunnelConnectError("tunnel_status_alias_mismatch")
    # Fail closed on absent fields, string booleans, stale process records.
    for field in ("process_running", "healthy", "ready"):
        if payload.get(field) is not True:
            raise TunnelConnectError("tunnel_local_runtime_not_ready")
    return {"process_running": True, "healthy": True, "ready": True}


def apply_windows_file_acl(path: Path) -> None:
    """Restrict the Authorization bearer file to the current Windows SID."""
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    try:
        identity = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],
            capture_output=True, text=True, timeout=8)
        sid = identity.stdout.strip()
        if identity.returncode != 0 or not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
            raise TunnelConnectError("cannot_resolve_windows_identity")
        result = subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r",
             "/grant:r", "*" + sid + ":(R)"],
            capture_output=True, timeout=8)
        if result.returncode:
            raise TunnelConnectError("cannot_restrict_observer_header_acl")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TunnelConnectError("cannot_restrict_observer_header_acl") from exc


def materialize_header(state: Path, bearer: str) -> Path:
    """Use protected per-user AppData because tunnel uses file header lookup.

    Never include bearer bytes in a Git repository, log or command line.
    """
    from .observer_runtime import TOKEN_RE
    if TOKEN_RE.fullmatch(bearer) is None:
        raise TunnelConnectError("invalid_observer_bearer")
    state = state.resolve(strict=True)
    file = state / "a4-observer-authorization-header"
    if file.is_symlink() or (file.exists() and not file.is_file()):
        raise TunnelConnectError("unsafe_observer_header")
    expected = ("Bearer " + bearer + "\n").encode("ascii")
    if file.exists():
        if file.read_bytes() != expected:
            raise TunnelConnectError("observer_header_conflict")
        apply_windows_file_acl(file)
        return file
    # Restrict permissions on the EMPTY newly created file BEFORE any
    # secret bytes are written. Exclusive create prevents token overwrite.
    try:
        with file.open("xb") as out:
            apply_windows_file_acl(file)
            out.write(expected)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        file.unlink(missing_ok=True)
        raise
    return file


def validate_input(tunnel_id: str, bearer: str, api_key: str) -> None:
    from .observer_runtime import TOKEN_RE
    if not TUNNEL_ID_RE.fullmatch(tunnel_id):
        raise TunnelConnectError("invalid_dedicated_tunnel_id")
    if TOKEN_RE.fullmatch(bearer) is None:
        raise TunnelConnectError("missing_observer_bearer")
    if not api_key or any(x in api_key for x in ("\n", "\r", "\x00")):
        raise TunnelConnectError("missing_control_plane_key")


def connect(*, state: Path, tunnel_id: str, bearer: str, api_key: str,
            cli: Path | None = None, audit=audit_readonly,
            aliases=get_aliases, execute=subprocess.run,
            verify_status=runtime_status) -> dict:
    """Explicit user action only; unknown state remains durable, not retried."""
    validate_input(tunnel_id, bearer, api_key)
    state=state.resolve(strict=True)
    if state.is_symlink():
        raise TunnelConnectError("unsafe_state")
    receipt=state/"a4-observer-tunnel-attempt.json"
    if receipt.exists() or receipt.is_symlink():
        raise TunnelConnectError("existing_attempt_requires_manual_reconciliation")
    cli = (cli or find_cli()).resolve(strict=True)
    audit(PORT, bearer)
    if ALIAS in aliases(cli):
        raise TunnelConnectError("dedicated_alias_already_exists")
    # No plaintext bearer file: official tunnel-client supports
    # "Authorization: env:NAME" in MCP_EXTRA_HEADERS and inherits its
    # environment in the managed child. The bearer remains transient in
    # the process environment, with DPAPI as the only at-rest copy.
    command=[str(cli), "runtimes", "connect", "--alias", ALIAS,
             "--tunnel-id", tunnel_id, "--runtime-api-key",
             "env:CONTROL_PLANE_API_KEY", "--mcp-server-url",
             f"http://127.0.0.1:{PORT}/mcp"]
    attempt={"schema_version":1, "alias":ALIAS, "state":"attempted",
             "owner_action_required_to_retry":True, "local_port":PORT}
    # Exclusive receipt creation is the local cross-process admission lock.
    # Never use os.replace() for the first attempt: it could overwrite a
    # concurrent attempt between existence check and rename.
    try:
        with receipt.open("x",encoding="utf-8") as out:
            json.dump(attempt,out)
            out.flush()
            os.fsync(out.fileno())
    except FileExistsError as exc:
        raise TunnelConnectError("concurrent_tunnel_connect_attempt") from exc
    env=os.environ.copy()
    env.pop("LOCAL_MCP_OBSERVER_BEARER",None)
    env["CONTROL_PLANE_API_KEY"]=api_key
    env["HERMES_A4_OBSERVER_AUTHORIZATION"]="Bearer " + bearer
    env["MCP_EXTRA_HEADERS"]="Authorization: env:HERMES_A4_OBSERVER_AUTHORIZATION"
    env["MCP_DISCOVERY_EXTRA_HEADERS"]="Authorization: env:HERMES_A4_OBSERVER_AUTHORIZATION"
    try:
        result=execute(command,env=env,capture_output=True,timeout=75)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise TunnelConnectError("tunnel_result_unknown_reconcile_manually") from exc
    if result.returncode != 0:
        raise TunnelConnectError("tunnel_result_unknown_reconcile_manually")
    # Explicit local status is needed: exit code 0 is not process readiness.
    # Even after local status passes, cloud authorization and MCP E2E remain
    # unverified until the owner checks them from the ChatGPT connector.
    try:
        local=verify_status(cli,env=env)
        if not (isinstance(local,dict)
                and all(local.get(k) is True for k in
                        ("process_running","healthy","ready"))):
            raise TunnelConnectError("tunnel_local_runtime_not_ready")
    except (TunnelConnectError,OSError,ValueError,subprocess.TimeoutExpired):
        raise TunnelConnectError("tunnel_result_unknown_reconcile_manually")
    attempt["state"]="local_runtime_healthy_remote_e2e_unverified"
    nextfile=state/("a4-observer-tunnel-attempt." + uuid.uuid4().hex + ".json")
    with nextfile.open("x",encoding="utf-8") as out:
        json.dump(attempt,out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(nextfile,receipt)
    return {"ok":True, "alias":ALIAS, "command_accepted":True,
            "local_runtime_ready":True, "remote_e2e_verified":False,
            "needs_cloud_authorization":True}


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("connect",))
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--tunnel-id",required=True)
    parser.add_argument("--approved",action="store_true")
    args=parser.parse_args(argv)
    try:
        if not args.approved:
            raise TunnelConnectError("owner_consent_required")
        result=connect(state=args.state,tunnel_id=args.tunnel_id,
                       bearer=os.environ.get("LOCAL_MCP_OBSERVER_BEARER",""),
                       api_key=os.environ.get("CONTROL_PLANE_API_KEY",""))
        print(json.dumps(result))
        return 0
    except (TunnelConnectError,ObserverStartupError,OSError,ValueError):
        print(json.dumps({"ok":False,"error":"observer_tunnel_connection_not_confirmed"}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
