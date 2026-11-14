"""Exercise the actual A4 source bundle in an isolated temporary state.

This proves the pinned candidate can be verified, staged, and read back
without touching the live Relay/Gateway/MCP/Tunnel or upgrading any runtime.
Only hashes and aggregate counts are printed, never source or credentials.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from quickstart.a4.runtime_bundle import verify_candidate, manifest_for, _manifest_bytes
from quickstart.a4.runtime_stage import RuntimeStager, StageError

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
ARCHIVE = WORKSPACE / ".hermes-a4-runtime-v0.0.0-candidate.zip"
PINNED_ARCHIVE_SHA256 = "1d5455e424c2fda423e58edfc3d2e12aa8a13ee823fe9512465266599a841796"
PINNED_MANIFEST_SHA256 = "02a690fc1e49e9de4b4afe25238e96e9b20f327ef2419e3d311babdd703a6fba"
VERSION = "v0.0.0-candidate"


def verify() -> dict:
    if not ARCHIVE.is_file() or ARCHIVE.is_symlink():
        return {"status": "BLOCKED", "reason": "pinned_local_candidate_missing"}
    archive_sha = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    if archive_sha != PINNED_ARCHIVE_SHA256:
        return {"status": "BLOCKED", "reason": "candidate_hash_drift"}
    verified = verify_candidate(ARCHIVE, PINNED_MANIFEST_SHA256)
    latest_manifest, _ = manifest_for(ROOT, VERSION)
    current_manifest_sha = hashlib.sha256(_manifest_bytes(latest_manifest)).hexdigest()
    with tempfile.TemporaryDirectory(prefix="hermes-a4-runtime-isolated-") as td:
        stager = RuntimeStager(Path(td))
        staged = stager.stage(ARCHIVE, VERSION,
                             PINNED_MANIFEST_SHA256, PINNED_ARCHIVE_SHA256)
        revalidated = stager.verify(VERSION, PINNED_MANIFEST_SHA256)
    return {
        "status": "PASS",
        "real_candidate_staged_and_verified": True,
        "archive_pinned": True,
        "sources_in_archive": verified["files_verified"],
        "stage_status": staged["state"],
        "verify_status": revalidated["state"],
        "candidate_matches_current_source": current_manifest_sha == PINNED_MANIFEST_SHA256,
        "installed_on_pc": False,
        "active_runtime_switched": False,
        "clean_clone_e2e_verified": False,
        "privacy_review_completed": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify()))
    except (OSError, ValueError, StageError, RuntimeError) as exc:
        print(json.dumps({"status":"BLOCKED","reason":type(exc).__name__}))
        raise SystemExit(2)
