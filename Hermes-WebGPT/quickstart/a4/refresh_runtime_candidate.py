"""Refresh the local-only Hermes runtime source ZIP after source drift.

No runtime execution. No deployment, no GitHub upload, no browser changes.
Requires explicit --build. New archive path must not exist, and only the
24 fixed, validated Python source modules are included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from quickstart.a4.runtime_bundle import (
    BundleError, build_local_candidate, manifest_for, _manifest_bytes
)
from quickstart.a4.runtime_stage import RuntimeStager, StageError

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT.parent / ".hermes-a4-runtime-v0.0.1-candidate.zip"
VERSION = "v0.0.1-candidate"
OLD_ARCHIVE = ROOT.parent / ".hermes-a4-runtime-v0.0.0-candidate.zip"


def compare_to_previous(current_manifest: dict) -> list[str] | None:
    if not OLD_ARCHIVE.is_file() or OLD_ARCHIVE.is_symlink():
        return None
    with zipfile.ZipFile(OLD_ARCHIVE, "r") as z:
        previous = json.loads(z.read("manifest.json"))
    if not isinstance(previous, dict) or not isinstance(previous.get("files"), list):
        return None
    before = {v["path"]: v["sha256"] for v in previous["files"]
              if isinstance(v, dict) and "path" in v and "sha256" in v}
    return sorted(x["path"] for x in current_manifest["files"]
                  if before.get(x["path"]) != x["sha256"])


def refresh(build: bool = False) -> dict:
    current, _ = manifest_for(ROOT, VERSION)
    diff = compare_to_previous(current)
    expected_manifest_sha = hashlib.sha256(_manifest_bytes(current)).hexdigest()
    if not build:
        return {
            "status": "READY_TO_BUILD",
            "files": len(current["files"]),
            "changed_module_count": None if diff is None else len(diff),
            "changed_module_names": diff,
            "output_already_exists": OUTPUT.exists(),
            "local_only": True,
        }
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise BundleError("new_candidate_already_exists_do_not_overwrite")
    built = build_local_candidate(ROOT, OUTPUT, VERSION, explicit_local_only=True)
    if built["manifest_sha256"] != expected_manifest_sha:
        raise BundleError("source_drift_during_local_build")
    # Actual locally produced archive must stage cleanly in a *temporary*
    # location without altering live runtime selection or process state.
    with tempfile.TemporaryDirectory(prefix="hermes-a4-refreshed-offline-") as tmp:
        stager = RuntimeStager(Path(tmp))
        staged = stager.stage(OUTPUT, VERSION, built["manifest_sha256"], built["zip_sha256"])
        check = stager.verify(VERSION, built["manifest_sha256"])
    return {
        "status": "LOCAL_CANDIDATE_STAGED_VERIFIED",
        "version": VERSION,
        "files": built["files"],
        "changed_module_count": None if diff is None else len(diff),
        "changed_module_names": diff,
        "manifest_sha256": built["manifest_sha256"],
        "archive_sha256": built["zip_sha256"],
        "staged_isolated": staged["state"] == "STAGED_NOT_ACTIVATED",
        "reverified_isolated": check["state"] == "VERIFIED_NOT_ACTIVATED",
        "published": False,
        "active_runtime_switched": False,
        "clean_clone_e2e_verified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args.build), ensure_ascii=False))
    except (BundleError, StageError, OSError, ValueError, KeyError,
            zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        raise SystemExit(2)
