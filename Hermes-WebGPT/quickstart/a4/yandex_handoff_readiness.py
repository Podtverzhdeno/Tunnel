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


def _source_manifest(path_hint: str, trusted_roots: tuple[Path, ...]) -> dict:
    """Read only an installed extension's manifest from trusted local roots.

    Yandex may store a partial manifest in Secure Preferences. The source
    manifest is another observation, NOT proof the currently loaded service
    worker uses that source, nor a permission/Send acceptance.
    """
    if not path_hint or len(path_hint) > 1024:
        return {}
    try:
        source = Path(path_hint)
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            return {}
        if hasattr(source, "is_junction") and source.is_junction():
            return {}
        real = source.resolve(strict=True)
        approved = False
        for candidate in trusted_roots:
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            if real.is_relative_to(candidate.resolve(strict=True)):
                approved = True
                break
        if not approved:
            return {}
        file = real / "manifest.json"
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 100_000:
            return {}
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("manifest_version") != 3:
            return {}
        name = data.get("name")
        if not isinstance(name, str) or "autopilot" not in name.casefold():
            return {}
        return data
    except (OSError, ValueError, UnicodeError, TypeError, AttributeError):
        return {}


def observe_profiles(user_data: Path, *,
                     trusted_source_roots: tuple[Path, ...] | None = None) -> dict:
    empty = {"profile_count": 0, "autopilot_registered": 0,
             "autopilot_versions": [], "source_versions_observed": [],
             "declared_content_scripts": 0,
             "complete_manifest_metadata": 0, "explicit_empty_content_scripts": 0,
             "source_manifests_observed": 0,
             "source_declared_content_scripts": 0,
             "source_safe_demo_markers": 0,
             "explicit_demo_markers": 0, "send_capability_proven": False}
    if user_data.is_symlink() or not user_data.is_dir():
        return empty
    if trusted_source_roots is None:
        # Within the development workspace, or within Yandex's own user data.
        trusted_source_roots = (Path(__file__).resolve().parents[3], user_data)
    profiles = [user_data / "Default"]
    profiles += sorted(p for p in user_data.glob("Profile *")
                       if ALLOWED_PROFILE.fullmatch(p.name))[:30]
    total = 0
    detected = 0
    versions: set[str] = set()
    source_versions: set[str] = set()
    scripts_declared = 0
    complete_manifests = 0
    explicitly_empty_scripts = 0
    source_manifests_observed = 0
    source_scripts_declared = 0
    source_demo_markers = 0
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
            source_manifest = _source_manifest(path_hint, trusted_source_roots)
            if source_manifest:
                source_manifests_observed += 1
                source_scripts = source_manifest.get("content_scripts")
                if isinstance(source_scripts, list) and source_scripts:
                    source_scripts_declared += 1
                source_desc = source_manifest.get("description", "")
                if isinstance(source_desc, str) and "safe_demo" in source_desc.casefold():
                    source_demo_markers += 1
                source_version = _safe_version(source_manifest.get("version"))
                if source_version:
                    source_versions.add(source_version)
            v = _safe_version(manifest.get("version"))
            if v:
                versions.add(v)
            content_scripts = manifest.get("content_scripts")
            # Yandex Secure Preferences can contain only a partial manifest.
            # Missing metadata does not prove the extension lacks a script;
            # dynamic chrome.scripting injection is also possible.
            if v and isinstance(manifest.get("name"), str):
                complete_manifests += 1
                if "content_scripts" in manifest and content_scripts == []:
                    explicitly_empty_scripts += 1
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
            "source_versions_observed": sorted(source_versions),
            "declared_content_scripts": scripts_declared,
            "complete_manifest_metadata": complete_manifests,
            "explicit_empty_content_scripts": explicitly_empty_scripts,
            "source_manifests_observed": source_manifests_observed,
            "source_declared_content_scripts": source_scripts_declared,
            "source_safe_demo_markers": source_demo_markers,
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
             bridge: Callable[[], str] = bridge_probe,
             trusted_source_roots: tuple[Path, ...] | None = None) -> dict:
    profiles = observe_profiles(user_data, trusted_source_roots=trusted_source_roots)
    bridge_state = bridge()
    registered = profiles["autopilot_registered"] > 0
    scripts = (profiles["declared_content_scripts"] > 0 or
               profiles["source_declared_content_scripts"] > 0)
    if not registered:
        issue = "AUTOPILOT_NOT_REGISTERED"
    elif profiles["explicit_demo_markers"] or profiles["source_safe_demo_markers"]:
        issue = "SAFE_DEMO_NO_AUTOMATIC_SEND"
    elif (profiles["complete_manifest_metadata"] == 0
          and profiles["source_manifests_observed"] == 0):
        issue = "REGISTRY_MANIFEST_METADATA_INCOMPLETE"
    elif not scripts:
        issue = "NO_DECLARED_CONTENT_SCRIPT"
    else:
        issue = "HANDOFF_NOT_LIVE_VERIFIED"
    return {
        "status": issue,
        "autopilot_registered": registered,
        "autopilot_instances": profiles["autopilot_registered"],
        "registered_versions": profiles["autopilot_versions"],
        "source_versions_observed": profiles["source_versions_observed"],
        "registered_source_manifest_observed": profiles["source_manifests_observed"] > 0,
        "source_binding_to_running_worker_verified": False,
        "declared_content_scripts": scripts,
        "manifest_metadata_complete": profiles["complete_manifest_metadata"] > 0,
        "explicit_empty_content_scripts": profiles["explicit_empty_content_scripts"] > 0,
        "dynamic_content_script_injection_verified": False,
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
