"""Pinned upstream Hermes Agent core installer plan for A4 Windows GUI.

This module NEVER invokes the installer: a user-approved GUI action does
that only after validating the exact source revision and SHA256 below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

UPSTREAM_REPO = "NousResearch/hermes-agent"
UPSTREAM_COMMIT = "ae3707b97e50e65c2031cff6b84ce5fc81024d11"
UPSTREAM_PATH = "scripts/install.ps1"
EXPECTED_SHA256 = "31dd1373eae71908cac5326d4ffd8adc85d131ff509916df30b2836e4e231b0f"


class BootstrapError(RuntimeError):
    pass


def installer_path(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parent) / "vendor" / "hermes-install.ps1"


def verified_installer(root: Path | None = None) -> Path:
    path = installer_path(root)
    if path.is_symlink() or not path.is_file():
        raise BootstrapError("pinned_installer_missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise BootstrapError("pinned_installer_hash_mismatch")
    license_path = path.parent / "LICENSE.NousResearch"
    if license_path.is_symlink() or not license_path.is_file():
        raise BootstrapError("upstream_license_missing")
    license_text = license_path.read_text(encoding="utf-8")
    if not license_text.startswith("MIT License"):
        raise BootstrapError("upstream_license_invalid")
    return path.resolve(strict=True)


def plan(root: Path | None = None) -> dict:
    path = verified_installer(root)
    return {
        "ok": True,
        "installer": str(path),
        "origin": f"https://github.com/{UPSTREAM_REPO}/blob/{UPSTREAM_COMMIT}/{UPSTREAM_PATH}",
        "sha256": EXPECTED_SHA256,
        "mode": "requires_explicit_user_consent",
        "command_args": ["-NonInteractive", "-SkipBrowser", "-SkipComputerUse", "-Channel", "stable"],
        "will_install": ["hermes-agent core", "validated upstream bootstrap dependencies"],
        "will_not_install": ["MCP host", "Tunnel", "Relay", "Yandex extension"],
        "executed": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan",))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(plan()))
        return 0
    except (BootstrapError, OSError, UnicodeError):
        print(json.dumps({"ok": False, "error": "bootstrap_preflight_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
