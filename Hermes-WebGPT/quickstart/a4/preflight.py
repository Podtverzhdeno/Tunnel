"""Hermes A4 first-run, read-only preflight. Never starts/restarts components.

Only health and file metadata are observed. No environment secrets, auth
headers, process command lines, or user files are included in JSON output.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.request import build_opener, ProxyHandler, Request

CURRENT_SCHEMA = 1
MODES = frozenset(("read_only", "full_access"))


class PreflightError(ValueError):
    pass


def validate_settings(raw: dict[str, Any], *, project: Path) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != CURRENT_SCHEMA:
        raise PreflightError("unsupported_settings_schema")
    mode = raw.get("mode")
    if mode not in MODES:
        raise PreflightError("invalid_mode")
    workers = raw.get("workers")
    if type(workers) is not int or not 1 <= workers <= 5:
        raise PreflightError("workers_out_of_range")
    if raw.get("browser") != "yandex":
        raise PreflightError("unsupported_browser")
    browser_path = raw.get("browser_exe")
    if not isinstance(browser_path, str) or not browser_path:
        raise PreflightError("yandex_path_not_configured")
    browser = Path(browser_path)
    if browser.name.lower() != "browser.exe" or not browser.is_file():
        raise PreflightError("yandex_executable_missing")
    # Avoid arbitrary untrusted executables masquerading as Yandex.
    if "yandexbrowser" not in (str(browser.parent).lower().replace("\\", "/") +
                              "/" + str(browser.parent.parent).lower().replace("\\", "/")):
        raise PreflightError("unrecognized_yandex_install_path")
    scope_raw = raw.get("scope")
    if not isinstance(scope_raw, str) or not scope_raw:
        raise PreflightError("scope_missing")
    scope = Path(scope_raw)
    if not scope.is_absolute() or not scope.is_dir():
        raise PreflightError("invalid_scope")
    # Parent of the checkout is the initial project-list workspace boundary.
    workspace = project.resolve(strict=True).parent
    actual = scope.resolve(strict=True)
    if not actual.is_relative_to(workspace):
        raise PreflightError("scope_outside_installer_workspace")
    if raw.get("enforcement") != "pending_backend_integration":
        # The field is advisory only, not an activation token.
        raise PreflightError("unexpected_enforcement_marker")
    return {"schema_version": CURRENT_SCHEMA, "mode": mode, "workers": workers,
            "browser": "yandex", "browser_exe": str(browser.resolve()),
            "scope": str(actual), "enforcement": "unverified"}


def _health(url: str, *, timeout: float = 1) -> Any:
    req = Request(url, method="GET")
    opener = build_opener(ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        if response.status != 200:
            return None
        data = response.read(2048)
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        return data.decode("ascii", errors="ignore").strip()


def inspect_services(fetch=_health) -> dict[str, bool]:
    endpoints = {
        "mcp": ("http://127.0.0.1:8765/healthz", lambda r:
                isinstance(r, dict) and (r.get("status") == "ok" or r.get("ok") is True)
                and isinstance(r.get("tools"), int) and r["tools"] >= 1),
        "tunnel": ("http://127.0.0.1:8080/readyz", lambda r:
                   r == "ready" or isinstance(r, dict)
                   and (r.get("ready") is True or r.get("status") == "ready"
                        or r.get("state") == "ready")),
        "relay": ("http://127.0.0.1:18765/health", lambda r:
                  isinstance(r, dict) and r.get("service") == "hermes-webgpt-relay"
                  and r.get("protocol") == 1),
    }
    result = {}
    for name, (url, predicate) in endpoints.items():
        try:
            result[name] = bool(predicate(fetch(url)))
        except (OSError, TimeoutError, ValueError):
            result[name] = False
    return result


def inspect_local_assets(project: Path) -> dict[str, bool]:
    """Source inventory only; do not run unsigned install scripts."""
    root=project.resolve(strict=True)
    host=root.parent/"Local-Workspace-Extensions"/"mcp-host-source"
    return {
        "relay_source": (root/"relay.py").is_file(),
        "relay_launcher": (root/"start_bridge.py").is_file(),
        "gateway_source": (root/"gateway_runner.py").is_file(),
        "local_mcp_source": (host/"manage.py").is_file(),
        "oneclick_launcher": (root/"quickstart"/"start.ps1").is_file(),
        "a4_installer": (root/"Setup-Hermes-A4.cmd").is_file(),
    }


def preflight(config: dict[str, Any], *, project: Path, fetch=_health) -> dict[str, Any]:
    data = validate_settings(config, project=project)
    stages = inspect_services(fetch)
    assets = inspect_local_assets(project)
    blockers=["mcp_authorization_boundary_unverified",
              "browser_extension_and_user_login_unverified",
              "live_telegram_delivery_unverified"]
    if not all(assets.values()):
        blockers.append("required_source_components_missing")
    return {
        "ok": True,
        "settings_valid": True,
        "permission_mode": data["mode"],
        "workers": data["workers"],
        "services": stages,
        "source_assets": assets,
        "browser_path_valid": True,
        "ready_for_live_start": False,
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.settings.open(encoding="utf-8-sig") as handle:
            config = json.load(handle)
        result = preflight(config, project=args.project)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (PreflightError, OSError, ValueError, TypeError):
        # Fail closed and do not include filenames/secrets in exception text.
        print(json.dumps({"ok": False, "error": "preflight_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
