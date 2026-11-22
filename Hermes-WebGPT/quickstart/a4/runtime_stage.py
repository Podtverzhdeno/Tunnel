"""Stage a verified offline Hermes-WebGPT Python runtime candidate.

This operation NEVER starts Relay, Gateway, MCP, Tunnel, Telegram or browser.
A runtime candidate requires independently reviewed source and externally
pinned archive+manifest SHA256. It cannot be promoted to production simply
because local extraction passes checksum validation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .runtime_bundle import (
    BundleError, MAX_TOTAL_BYTES, SAFE_NAMES, PRODUCT, SCHEMA,
    verify_candidate,
)
from .release_manager import HASH, VERSION


class StageError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_dir(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise StageError("invalid_runtime_state")
    if hasattr(path, "is_junction") and path.is_junction():
        raise StageError("runtime_state_junction_denied")
    return path.resolve(strict=True)


class RuntimeStager:
    def __init__(self, state: Path):
        self.state = _safe_dir(Path(state))
        self.versions = self.state / "a4-runtime-versions"
        if self.versions.exists() or self.versions.is_symlink():
            _safe_dir(self.versions)
        self.pointer = self.state / "a4-runtime-selected.json"
        self.lock = self.state / "a4-runtime-stage.lock"

    def _lock(self) -> None:
        try:
            with self.lock.open("x", encoding="utf8") as out:
                out.write("runtime_stage_in_progress\n")
                out.flush()
                os.fsync(out.fileno())
        except FileExistsError as exc:
            raise StageError("runtime_stage_busy_or_interrupted") from exc

    def stage(self, archive: Path, version: str,
              manifest_sha256: str, archive_sha256: str) -> dict:
        if not VERSION.fullmatch(version) or not (
                isinstance(manifest_sha256,str) and HASH.fullmatch(manifest_sha256)
                and isinstance(archive_sha256,str) and HASH.fullmatch(archive_sha256)):
            raise StageError("trusted_runtime_pins_required")
        path=Path(archive)
        if path.is_symlink() or not path.is_file():
            raise StageError("runtime_archive_missing")
        if path.stat().st_size > 2*MAX_TOTAL_BYTES:
            raise StageError("runtime_archive_too_large")
        raw=path.read_bytes()
        if _sha256(raw)!=archive_sha256:
            raise StageError("runtime_archive_hash_mismatch")
        # verify_candidate opens the caller's path; we stage the exact pinned
        # captured bytes under a private unique temp directory to avoid TOCTOU.
        self._lock()
        try:
            self.versions.mkdir(exist_ok=True)
            _safe_dir(self.versions)
            target=self.versions/version
            if target.exists() or target.is_symlink():
                raise StageError("runtime_version_already_staged")
            stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=self.versions))
            try:
                transient=stage/"input.zip"
                with transient.open("xb") as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    verification=verify_candidate(transient,manifest_sha256)
                except (BundleError,zipfile.BadZipFile,ValueError) as exc:
                    raise StageError("runtime_candidate_validation_failed") from exc
                if verification.get("files_verified")!=len(SAFE_NAMES):
                    raise StageError("runtime_file_count_invalid")
                with zipfile.ZipFile(io.BytesIO(raw),"r") as z:
                    manifest=json.loads(z.read("manifest.json"))
                    if (manifest.get("version")!=version or
                        manifest.get("product")!=PRODUCT or
                        manifest.get("schema_version")!=SCHEMA):
                        raise StageError("runtime_candidate_version_mismatch")
                    spec={entry["path"]:entry for entry in manifest["files"]}
                    for name in sorted(SAFE_NAMES):
                        data=z.read(name)
                        if len(data)!=spec[name]["size"] or _sha256(data)!=spec[name]["sha256"]:
                            raise StageError("runtime_payload_hash_mismatch")
                        # Compile before exposing any stage, but never run it.
                        try:
                            compile(data,name,"exec")
                        except (SyntaxError,UnicodeError) as exc:
                            raise StageError("runtime_syntax_invalid") from exc
                        outpath=stage/name
                        with outpath.open("xb") as out:
                            out.write(data)
                            out.flush()
                            os.fsync(out.fileno())
                    with (stage/"manifest.json").open("xb") as out:
                        out.write(z.read("manifest.json"))
                        out.flush()
                        os.fsync(out.fileno())
                transient.unlink()
                os.rename(stage,target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            # Stage does not change an active runtime or automatically
            # activate/start any process. Production acceptance is a separate
            # owner-authorized process and A5 review.
            return {"ok":True,"state":"STAGED_NOT_ACTIVATED",
                    "version":version,"files_verified":len(SAFE_NAMES),
                    "manifest_sha256":manifest_sha256,
                    "remote_delivery_verified":False,
                    "independent_review_required":True}
        finally:
            self.lock.unlink(missing_ok=True)

    def verify(self, version: str, manifest_sha256: str) -> dict:
        if not VERSION.fullmatch(version) or not HASH.fullmatch(manifest_sha256):
            raise StageError("trusted_runtime_pin_required")
        target=self.versions/version
        root=_safe_dir(target)
        expected=set(SAFE_NAMES)|{"manifest.json"}
        observed=set()
        for item in root.iterdir():
            if item.is_symlink() or not item.is_file():
                raise StageError("staged_runtime_nonfile_or_link")
            observed.add(item.name)
        if observed!=expected:
            raise StageError("staged_runtime_inventory_changed")
        manifest_raw=(root/"manifest.json").read_bytes()
        if _sha256(manifest_raw)!=manifest_sha256:
            raise StageError("staged_runtime_manifest_changed")
        data=json.loads(manifest_raw)
        if data.get("version")!=version or data.get("product")!=PRODUCT:
            raise StageError("staged_runtime_schema_invalid")
        spec={entry["path"]:entry for entry in data.get("files",[])}
        if set(spec)!=SAFE_NAMES or len(data.get("files",[]))!=len(SAFE_NAMES):
            raise StageError("staged_runtime_manifest_inventory_changed")
        for name in SAFE_NAMES:
            content=(root/name).read_bytes()
            if _sha256(content)!=spec[name]["sha256"] or len(content)!=spec[name]["size"]:
                raise StageError("staged_runtime_file_changed")
        return {"ok":True, "state":"VERIFIED_NOT_ACTIVATED",
                "version":version,"files_verified":len(SAFE_NAMES)}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("stage","verify"))
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--archive",type=Path)
    parser.add_argument("--version",required=True)
    parser.add_argument("--manifest-sha256",required=True)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--approved",action="store_true")
    args=parser.parse_args(argv)
    try:
        stager=RuntimeStager(args.state)
        if args.action=="stage":
            if not args.approved or args.archive is None:
                raise StageError("explicit_owner_stage_approval_required")
            result=stager.stage(args.archive,args.version,
                                args.manifest_sha256,args.archive_sha256)
        else:
            result=stager.verify(args.version,args.manifest_sha256)
        print(json.dumps(result))
        return 0
    except (StageError,BundleError,OSError,ValueError,TypeError,
            zipfile.BadZipFile):
        print(json.dumps({"ok":False,"error":"runtime_stage_blocked"}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
