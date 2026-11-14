"""A4 browser extension intake: distinguish a loadable candidate from a release.

Read-only and source-bound. Never opens or modifies a Yandex profile,
installs a CRX, or treats a SAFE_DEMO build as production functionality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ALLOWED_PERMISSIONS = frozenset({
    "tabs", "storage", "alarms", "scripting", "contextMenus",
})
ALLOWED_HOSTS = frozenset({
    "https://chatgpt.com/*",
    "http://127.0.0.1:18763/*", "http://127.0.0.1:18764/*",
    "http://127.0.0.1:18765/*", "http://127.0.0.1:18766/*",
})
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ExtensionError(RuntimeError):
    pass


def _asset(root: Path, path: str) -> Path:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise ExtensionError("unsafe_extension_asset_path")
    rel=PurePosixPath(path)
    if (rel.is_absolute() or ".." in rel.parts or ":" in path
            or rel.as_posix()!=path):
        raise ExtensionError("unsafe_extension_asset_path")
    candidate=root.joinpath(*rel.parts)
    if candidate.is_symlink() or not candidate.is_file() or not (
            candidate.resolve(strict=True).is_relative_to(root)):
        raise ExtensionError("extension_asset_missing_or_outside")
    return candidate


def inspect_extension(path: Path, *, approved_manifest_sha256: str | None = None) -> dict:
    root=Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ExtensionError("extension_directory_invalid")
    if hasattr(root,"is_junction") and root.is_junction():
        raise ExtensionError("extension_junction_invalid")
    root=root.resolve(strict=True)
    manifest_file=_asset(root,"manifest.json")
    raw=manifest_file.read_bytes()
    if len(raw)>200_000:
        raise ExtensionError("extension_manifest_too_large")
    digest=hashlib.sha256(raw).hexdigest()
    try:
        m=json.loads(raw)
    except (UnicodeError,ValueError) as exc:
        raise ExtensionError("invalid_extension_manifest") from exc
    if (not isinstance(m,dict) or m.get("manifest_version")!=3
            or not isinstance(m.get("name"),str)
            or not VERSION_RE.fullmatch(str(m.get("version","")))):
        raise ExtensionError("unsupported_extension_manifest")
    if m.get("update_url") or m.get("externally_connectable"):
        raise ExtensionError("extension_external_update_or_connect_denied")
    perms=m.get("permissions",[])
    hosts=m.get("host_permissions",[])
    if (not isinstance(perms,list) or not isinstance(hosts,list)
            or any(not isinstance(p,str) for p in perms+hosts)
            or len(set(perms))!=len(perms) or len(set(hosts))!=len(hosts)
            or not set(perms).issubset(ALLOWED_PERMISSIONS)
            or not set(hosts).issubset(ALLOWED_HOSTS)
            or "https://chatgpt.com/*" not in hosts):
        raise ExtensionError("extension_permissions_not_allowlisted")
    assets=set()
    background=m.get("background")
    if not isinstance(background,dict) or not isinstance(
            background.get("service_worker"),str):
        raise ExtensionError("extension_service_worker_required")
    assets.add(background["service_worker"])
    action=m.get("action")
    if isinstance(action,dict) and action.get("default_popup"):
        assets.add(action["default_popup"])
    scripts=m.get("content_scripts",[])
    if not isinstance(scripts,list):
        raise ExtensionError("invalid_content_scripts")
    for script in scripts:
        if not isinstance(script,dict):
            raise ExtensionError("invalid_content_script")
        if not set(script.get("matches",[])).issubset({"https://chatgpt.com/*"}):
            raise ExtensionError("content_script_origin_not_allowed")
        files=script.get("js",[])
        if not isinstance(files,list):
            raise ExtensionError("invalid_content_script_js")
        assets.update(files)
    for asset in assets:
        _asset(root,asset)
    # This is a source intake check, not a user-grant or functionality test.
    desc=str(m.get("description",""))
    demo_marker=bool(re.search(r"(?i)\b(?:SAFE_DEMO|demo.only|not automatically sent)\b",
                               desc+" "+str(m.get("name",""))))
    hash_matches=bool(approved_manifest_sha256 and
                      HASH_RE.fullmatch(approved_manifest_sha256) and
                      approved_manifest_sha256==digest)
    # A digest equality is not a trusted identity, human approval or live
    # browser test. Never manufacture a release approval from caller input.
    return {
        "ok":True,"name":m["name"],"version":m["version"],
        "manifest_sha256":digest,"files_checked":len(assets)+1,
        "manifest_hash_matches_expected":hash_matches,
        "demo_only":demo_marker,
        "eligible_for_independent_review":hash_matches and not demo_marker,
        "production_candidate":False,
        "independent_a5_review_required":True,
        "installed_in_yandex":False,
        "user_authorization_verified":False,
        "chatgpt_message_delivery_verified":False,
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--extension",type=Path,required=True)
    p.add_argument("--approved-manifest-sha256",default=None)
    opts=p.parse_args(argv)
    try:
        print(json.dumps(inspect_extension(
            opts.extension,approved_manifest_sha256=opts.approved_manifest_sha256)))
        return 0
    except (ExtensionError,OSError,ValueError,TypeError):
        print(json.dumps({"ok":False,"error":"extension_intake_blocked"}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
