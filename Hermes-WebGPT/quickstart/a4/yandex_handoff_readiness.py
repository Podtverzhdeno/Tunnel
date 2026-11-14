"""Read-only Yandex AutoPilot handoff readiness and blank-tab diagnostics.

Unlike an extension inventory, a registered AutoPilot extension does not
prove a content script executes, a Web Companion is attached, or a prompt
has been sent. Do not load private page contents or modify browser state.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from typing import Callable

MAX_PREF_BYTES = 20 * 1024 * 1024
ALLOWED_PROFILE = re.compile(r"^(?:Default|Profile [0-9]{1,3})$")
VERSION = re.compile(r"^[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}$")
URL = "http://127.0.0.1:18763/v1/health"


def _safe_version(value: object) -> str | None:
    return value if isinstance(value, str) and VERSION.fullmatch(value) else None


def _registry(path: Path) -> dict:
    """Only read extension metadata from a browser-owned preferences file.

    The output never exposes extension IDs, profile paths, names of other
    extensions, account/cookie state, or any source contents.
    """
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PREF_BYTES:
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        ext = obj.get("extensions", {}).get("settings", {})
        return ext if isinstance(ext, dict) else {}
    except (OSError, UnicodeError, ValueError, AttributeError, TypeError):
        return {}


def observe_profiles(user_data: Path) -> dict:
    if user_data.is_symlink() or not user_data.is_dir():
        return {"profile_count": 0, "autopilot_registered": 0,
                "autopilot_versions": [], "send_capability_proven": False}
    profiles = [user_data / "Default"]
    profiles += sorted(p for p in user_data.glob("Profile *")
                       if ALLOWED_PROFILE.fullmatch(p.name))[:30]
    total = 0
    detected = 0
    versions: set[str] = set()
    scripts_declared = 0
    demo_markers = 0
    for profile in profiles:
        if profile.is_symlink() or not profile.is_dir():
            continue
        total += 1
        # Secure Preferences is preferred, because it has actual extension
        # registration records on Yandex; do not merge two snapshots.
        secure = profile / "Secure Preferences"
        registry = _registry(secure)
        if not registry:
            registry = _registry(profile / "Preferences")
        for value in registry.values():
            if not isinstance(value, dict):
                continue
            manifest = value.get("manifest", {})
            if not isinstance(manifest, dict):
                manifest = {}
            name = str(manifest.get("name", ""))
            path_hint = value.get("path", "")
            if not isinstance(path_hint, str):
                path_hint = ""
            if "autopilot" not in name.casefold() and "autopilot" not in path_hint.casefold():
                continue
            detected += 1
            v = _safe_version(manifest.get("version"))
            if v:
                versions.add(v)
            content_scripts = manifest.get("content_scripts")
            if isinstance(content_scripts, list) and content_scripts:
                scripts_declared += 1
            desc = manifest.get("description", "")
            if isinstance(desc, str) and (
                "safe_demo" in desc.casefold() or "не отправлено" in desc.casefold()
            ):
                demo_markers += 1
    return {"profile_count": total,
            "autopilot_registered": detected,
            "autopilot_versions": sorted(versions),
            "declared_content_scripts": scripts_declared,
            "explicit_demo_markers": demo_markers,
            "send_capability_proven": False}


def bridge_probe() -> str:
    """403 means AUTH_REQUIRED, not that the Bridge is down."""
    req = urllib.request.Request(URL, method="GET")
    try:
        with urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        ).open(req, timeout=2) as response:
            if response.status == 200:
                return "HTTP_OK_UNAUTHENTICATED_NOT_E2E"
            return "UNEXPECTED_HTTP_STATUS"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "AUTH_REQUIRED"
        return "HTTP_ERROR"
    except (OSError, ValueError, TimeoutError):
        return "UNREACHABLE"


def evaluate(*, user_data: Path,
             bridge: Callable[[], str] = bridge_probe) -> dict:
    profiles = observe_profiles(user_data)
    bridge_state = bridge()
    registered = profiles["autopilot_registered"] > 0
    scripts = profiles["declared_content_scripts"] > 0
    if not registered:
        issue = "AUTOPILOT_NOT_REGISTERED"
    elif profiles["explicit_demo_markers"]:
        issue = "SAFE_DEMO_NO_AUTOMATIC_SEND"
    elif not scripts:
        issue = "NO_CONTENT_SCRIPT_HANDOFF"
    else:
        issue = "HANDOFF_NOT_LIVE_VERIFIED"
    return {
        "status": issue,
        "autopilot_registered": registered,
        "autopilot_instances": profiles["autopilot_registered"],
        "registered_versions": profiles["autopilot_versions"],
        "declared_content_scripts": scripts,
        "web_companion_bridge": bridge_state,
        "dev_launch_blocked": False,
        "prompt_dispatched": False,
        "chatgpt_response_verified": False,
        "telegram_delivery_verified": False,
        "next_action": (
            "VERIFY_NON_DEMO_AUTOPILOT_TO_WEB_COMPANION_HANDOFF"
            if registered else "INSTALL_AUTOPILOT_WITH_USER_CONSENT"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Yandex" / "YandexBrowser" / "User Data")
    report = evaluate(user_data=path)
    print(json.dumps(report, ensure_ascii=False))
    # Diagnostic does not stop DEV launch; only indicates missing e2e.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
