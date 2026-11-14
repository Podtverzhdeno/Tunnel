"""Prepare a standalone read-only HTTP MCP host configuration.

This does NOT expose a new port or reconfigure the existing 28-tool runtime.
The consumer must explicitly route a trusted observer transport to this
config, with a protected bearer and without a parallel unrestricted tool path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .preflight import PreflightError, validate_settings


class ObserverConfigError(RuntimeError):
    pass


def observer_config(settings: dict[str, Any], project: Path) -> dict[str, Any]:
    valid = validate_settings(settings, project=project)
    if valid["mode"] != "read_only":
        raise ObserverConfigError("observer_requires_read_only_mode")
    return {
        "roots": [{
            "name": "hermes",
            "path": valid["scope"],
            "read": True,
            "write": False,
            "execute": False,
            "git": False,
        }]
    }


def prepare(settings: dict[str, Any], project: Path, destination: Path) -> dict[str, Any]:
    cfg = observer_config(settings, project)
    project_root = project.resolve(strict=True).parent
    destination = Path(destination)
    if not destination.parent.is_dir() or destination.is_symlink():
        raise ObserverConfigError("invalid_config_destination")
    target = destination.resolve(strict=False)
    # Observer configs must be kept OUTSIDE any visible tool workspace.
    if target.is_relative_to(project_root):
        raise ObserverConfigError("config_in_exposed_workspace")
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ObserverConfigError("existing_config_not_valid_json") from exc
        if existing != cfg:
            raise ObserverConfigError("existing_config_differs_manual_reconcile")
        return {"ok": True, "state": "already_prepared", "write": False}
    temporary = destination.with_name(destination.name + ".new")
    if temporary.exists() or temporary.is_symlink():
        raise ObserverConfigError("stale_temp_requires_reconciliation")
    try:
        with temporary.open("x", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"ok": True, "state": "prepared", "write": True}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        cfg = json.loads(args.settings.read_text(encoding="utf-8-sig"))
        print(json.dumps(prepare(cfg, args.project, args.destination)))
        return 0
    except (PreflightError, ObserverConfigError, OSError, ValueError):
        print(json.dumps({"ok": False, "error": "observer_preparation_blocked"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
