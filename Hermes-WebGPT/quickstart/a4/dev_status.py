"""Read-only operational readiness for Hermes DEV on Windows.

Does not send Telegram messages, execute models, open tabs, read credentials,
start services or modify profiles. A locally healthy port is not proof of
Telegram/Web ChatGPT delivery.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import urllib.request
from typing import Callable, Any

# Local Workspace and the WinForms DEV-status button execute this file by
# path. Keep the same entrypoint usable with both "python file.py" and
# "python -m quickstart.a4.dev_status" from the checkout.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from quickstart import services
from quickstart.a4.relay_capacity_observer import capacity_report

ENDPOINTS = {
    "mcp": "http://127.0.0.1:8765/healthz",
    "tunnel_ready": "http://127.0.0.1:8080/readyz",
    "tunnel_control": "http://127.0.0.1:8080/health/control-plane",
    "relay": "http://127.0.0.1:18765/health",
}


def _get(url: str) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.build_opener(
        urllib.request.ProxyHandler({})
    ).open(req, timeout=2) as resp:
        if resp.status != 200:
            raise ValueError("unexpected_http_status")
        raw = resp.read(4096).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            return raw.strip()


def status(*, fetch: Callable[[str], Any] = _get,
           gateway_plan: Callable[[], dict] = services.gateway_plan,
           capacity_probe: Callable[[], dict] = capacity_report,
           project: Path | None = None) -> dict:
    values = {}
    for name, url in ENDPOINTS.items():
        try:
            values[name] = fetch(url)
        except (OSError, ValueError, TimeoutError):
            values[name] = None
    mcp = values["mcp"]
    relay = values["relay"]
    tunnel = values["tunnel_ready"]
    ctrl = values["tunnel_control"]
    mcp_ok = isinstance(mcp, dict) and (
        mcp.get("status") == "ok" or mcp.get("ok") is True
    ) and type(mcp.get("tools")) is int and mcp["tools"] >= 1
    relay_ok = isinstance(relay, dict) and (
        relay.get("service") == "hermes-webgpt-relay"
        and relay.get("protocol") == 1
    )
    transport_ready = (
        tunnel == "ready" or isinstance(tunnel, dict)
        and (tunnel.get("ready") is True or tunnel.get("status") == "ready")
    )
    try:
        gw = gateway_plan()
        gateway_ok = isinstance(gw, dict) and gw.get("action") == "already_running"
    except (services.StartupBlocked, OSError, ValueError):
        gateway_ok = False

    # Present only nonsecret summaries. 'polling' is an observable tunnel
    # control-plane state, not an assertion that cloud calls succeed.
    ctrl_state = None
    ctrl_reason = None
    if isinstance(ctrl, dict):
        if ctrl.get("state") in ("polling", "ready", "degraded", "backoff"):
            ctrl_state = ctrl["state"]
        if ctrl.get("reason_code") in ("network_error", "auth_error"):
            ctrl_reason = ctrl["reason_code"]

    try:
        relay_cap = capacity_probe()
        if not isinstance(relay_cap, dict):
            relay_cap = {}
    except (OSError, ValueError, TypeError):
        relay_cap = {}
    confirmed_slots = (relay_cap.get("running_relay_slots")
                       if relay_cap.get("local_relay_authenticated") is True
                       else None)
    if type(confirmed_slots) is not int or not 1 <= confirmed_slots <= 25:
        confirmed_slots = None
    checks = {
        "mcp": bool(mcp_ok),
        "tunnel_local": bool(transport_ready),
        "relay": bool(relay_ok),
        "gateway_process": bool(gateway_ok),
    }
    return {
        "local_components": checks,
        "local_ready": all(checks.values()),
        "mcp_tools_observed": mcp["tools"] if mcp_ok else None,
        "relay_running_slots_authenticated": confirmed_slots,
        "relay_three_slot_capacity_verified": (
            confirmed_slots is not None and confirmed_slots >= 3
        ),
        "three_parallel_browser_requests_e2e_verified": False,
        "tunnel_control_state": ctrl_state,
        "tunnel_control_reason": ctrl_reason,
        "dev_mode_can_use_existing_services": all(checks.values()),
        "dev_permission_boundary_enforced": False,
        "browser_chatgpt_delivery_verified": False,
        "telegram_roundtrip_verified": False,
        "file_image_roundtrip_verified": False,
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = status()
    print(json.dumps(data, ensure_ascii=False))
    return 0 if data["local_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
