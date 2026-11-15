#!/usr/bin/env python3
"""Read-only localhost-only Local Workspace/Tunnel disconnect doctor.

No credentials, auth headers, Cloud API, Telegram, browser or restart actions.
Only checks existing loopback health endpoints. Output is allowlisted metadata.
"""
from __future__ import annotations

import argparse
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 8192
TIMEOUT_SECONDS = 2.0
ENDPOINTS = (
    ("mcp", 8765, "/healthz"),
    ("tunnel_ready", 8080, "/readyz"),
    ("tunnel_mcp", 8080, "/health/mcp"),
    ("control_plane", 8080, "/health/control-plane"),
)
ALLOWED_STATUS = frozenset(("ready", "ok", "healthy", "connected", "degraded", "backoff", "unready", "error", "down", "up"))
ALLOWED_REASON = frozenset(("network_error", "dns_error", "auth_error", "unauthorized", "timeout", "connection_error", "backoff", "none", "unknown"))


def safe_status(value):
    """Never print unexpected values, even if endpoint sends secrets."""
    return value.lower() if type(value) is str and value.lower() in ALLOWED_STATUS else None


def safe_reason(value):
    return value.lower() if type(value) is str and value.lower() in ALLOWED_REASON else None


def evaluate_payload(data):
    if not isinstance(data, dict):
        return {"http_result": "invalid_json_shape"}
    status = safe_status(data.get("status"))
    reason = safe_reason(data.get("reason_code"))
    # Do not print token, user IDs, IPs, paths, environment variables, etc.
    result = {"http_result": "responded"}
    if status is not None:
        result["status"] = status
    if reason is not None:
        result["reason_code"] = reason
    if type(data.get("ok")) is bool:
        result["ok"] = data["ok"]
    if type(data.get("ready")) is bool:
        result["ready"] = data["ready"]
    if type(data.get("backoff")) is bool:
        result["backoff"] = data["backoff"]
    count = data.get("consecutive_failures")
    if type(count) is int and 0 <= count <= 1_000_000:
        result["consecutive_failures"] = count
    return result


def probe(port, path, opener=None):
    if type(port) is not int or not 1024 <= port <= 65535 or path not in {x[2] for x in ENDPOINTS}:
        raise ValueError("invalid loopback target")
    opener = opener if opener is not None else build_opener(ProxyHandler({}))
    url = "http://127.0.0.1:%d%s" % (port, path)
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return {"http_result": "response_too_large"}
            try:
                return evaluate_payload(json.loads(raw))
            except (ValueError, UnicodeError):
                return {"http_result": "not_json"}
    except HTTPError as error:
        return {"http_result": "http_error", "http_status": error.code}
    except (OSError, TimeoutError, URLError):
        return {"http_result": "not_reachable"}


def classify(checks):
    mcp = checks.get("mcp", {})
    tunnel = checks.get("tunnel_ready", {})
    control = checks.get("control_plane", {})
    if mcp.get("http_result") != "responded":
        diagnosis = "LOCAL_MCP_UNAVAILABLE"
        advice = "Check Local Workspace MCP service on 127.0.0.1:8765; do not reset its credentials."
    elif tunnel.get("http_result") != "responded":
        diagnosis = "TUNNEL_CLIENT_UNAVAILABLE"
        advice = "Check existing tunnel-client process and 127.0.0.1:8080/readyz; do not start duplicate instances."
    elif (control.get("status") in ("degraded", "backoff", "error")
          or control.get("backoff") is True
          or control.get("reason_code") in ("network_error", "dns_error", "auth_error", "unauthorized")):
        diagnosis = "TUNNEL_CONTROL_PLANE_DEGRADED"
        advice = "Review current Wi-Fi/VPN/DNS and tunnel-client doctor output; avoid restarting healthy MCP or replaying work."
    elif control.get("http_result") != "responded":
        diagnosis = "CONTROL_PLANE_STATUS_UNAVAILABLE"
        advice = "Loopback services responded, but control-plane health is unavailable; inspect tunnel-client locally."
    else:
        diagnosis = "LOOPBACK_SERVICES_RESPONDING_REMOTE_SESSION_UNVERIFIED"
        advice = "Local health alone cannot prove that the ChatGPT connector session is attached; check tunnel-client connection status."
    return {"diagnosis": diagnosis, "recommended_action": advice,
            "remote_chatgpt_session_attested": False, "read_only": True,
            "messages_sent": 0, "credentials_disclosed": False}


def dns_check(lookup=socket.getaddrinfo):
    """Optional DNS-only check; does not send HTTP traffic or reveal IPs."""
    try:
        records = lookup("api.openai.com", 443, type=socket.SOCK_STREAM)
        return {"dns_name_resolved": bool(records), "target": "api.openai.com"}
    except (OSError, TimeoutError):
        return {"dns_name_resolved": False, "target": "api.openai.com"}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--check-dns", action="store_true", help="Additionally resolve api.openai.com (DNS only, no HTTP)")
    opts = args.parse_args()
    checks = {name: probe(port, path) for name, port, path in ENDPOINTS}
    result = {"schema": "local-workspace-disconnect-doctor-v1", "checks": checks,
              **classify(checks)}
    if opts.check_dns:
        result["dns"] = dns_check()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0  # A diagnosis is informative; no state mutation.


if __name__ == "__main__":
    raise SystemExit(main())
