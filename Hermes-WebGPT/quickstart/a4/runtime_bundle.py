"""Offline, privacy-gated runtime SOURCE distribution for Hermes A4.

Creates only a *local* candidate ZIP of reviewed Python modules.
This is NOT an installer, and is never published/uploaded automatically.

The fixed allowlist prevents accidental inclusion of .env, logs, profiles,
Telegram allowlists, browser cookies or other adjacent user files.
A reviewer must independently inspect the source before using the candidate.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from .dependency_closure import REQUIRED_PY_FILES

SCHEMA = 1
PRODUCT = "Hermes-WebGPT-runtime-source"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 64
SAFE_NAMES = frozenset(REQUIRED_PY_FILES)

# Conservative common credentials: never report the matched value.
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{25,}"),
    re.compile(rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{28,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|bot[_-]?token|password)\s*[:=]\s*['\"][A-Za-z0-9_-]{20,}"),
)


class BundleError(RuntimeError):
    pass


def _safe_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise BundleError("invalid_source_root")
    if hasattr(root, "is_junction") and root.is_junction():
        raise BundleError("source_root_junction_denied")
    return root.resolve(strict=True)


def _bytes_from_source(root: Path) -> dict[str, bytes]:
    root = _safe_root(root)
    result = {}
    total = 0
    for name in sorted(SAFE_NAMES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise BundleError("required_source_module_missing")
        if not path.resolve(strict=True).is_relative_to(root):
            raise BundleError("source_path_outside_root")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise BundleError("source_too_large")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise BundleError("total_sources_too_large")
        if any(regex.search(data) for regex in SECRET_PATTERNS):
            raise BundleError("possible_secret_in_source_review_required")
        try:
            ast.parse(data.decode("utf-8-sig"))
        except (UnicodeError, SyntaxError) as exc:
            raise BundleError("invalid_python_source") from exc
        result[name] = data
    return result


def manifest_for(root: Path, version: str) -> tuple[dict, dict[str, bytes]]:
    from .release_manager import VERSION
    if not VERSION.fullmatch(version):
        raise BundleError("invalid_release_version")
    sources = _bytes_from_source(root)
    manifest = {
        "schema_version": SCHEMA, "product": PRODUCT, "version": version,
        "status": "candidate_requires_independent_source_review",
        "files": [
            {"path": key, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for key, data in sorted(sources.items())
        ],
    }
    return manifest, sources


def _manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf8")


def build_local_candidate(root: Path, output: Path, version: str,
                          *, explicit_local_only: bool = False) -> dict:
    if not explicit_local_only:
        raise BundleError("explicit_local_candidate_build_required")
    manifest, sources = manifest_for(root, version)
    output = Path(output)
    if output.is_symlink() or output.exists() or not output.parent.is_dir():
        raise BundleError("output_exists_or_parent_invalid")
    # Do not put candidate artifacts inside the source module root.
    srcroot = _safe_root(root)
    if output.resolve(strict=False).is_relative_to(srcroot):
        raise BundleError("do_not_bundle_into_source_root")
    if len(sources) > MAX_FILES:
        raise BundleError("file_count_limit")
    manifest_raw = _manifest_bytes(manifest)
    # No unsafe names are user-provided: the allowlist is closed.
    temp = output.with_name(output.name + ".new")
    if temp.exists() or temp.is_symlink():
        raise BundleError("stale_candidate_temp_requires_reconciliation")
    try:
        with temp.open("xb") as fp:
            with zipfile.ZipFile(fp, "w", compression=zipfile.ZIP_DEFLATED) as z:
                names = {"manifest.json": manifest_raw} | sources
                for name, data in sorted(names.items()):
                    item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                    item.compress_type = zipfile.ZIP_DEFLATED
                    item.external_attr = (stat.S_IFREG | 0o644) << 16
                    z.writestr(item, data)
            fp.flush()
            os.fsync(fp.fileno())
        # Verify own ZIP before exposing it to any external consumer.
        verify_candidate(temp, hashlib.sha256(manifest_raw).hexdigest())
        os.rename(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "ok": True, "state": "LOCAL_CANDIDATE_NOT_PUBLISHED",
        "version": version, "files": len(sources),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "zip_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "independent_review_required": True,
    }


def verify_candidate(zip_path: Path, expected_manifest_sha256: str) -> dict:
    from .release_manager import HASH
    if not isinstance(expected_manifest_sha256, str) or not HASH.fullmatch(expected_manifest_sha256):
        raise BundleError("trusted_manifest_hash_required")
    archive = Path(zip_path)
    if archive.is_symlink() or not archive.is_file():
        raise BundleError("candidate_not_file")
    with zipfile.ZipFile(archive, "r") as z:
        entries = z.infolist()
        expected_names = SAFE_NAMES | {"manifest.json"}
        names = [item.filename for item in entries]
        if len(entries) != len(expected_names) or set(names) != expected_names:
            raise BundleError("candidate_zip_inventory_mismatch")
        if len(names) != len(set(n.casefold() for n in names)):
            raise BundleError("candidate_duplicate_path")
        # A filename/hash allowlist is not enough: ZIP entries can carry Unix
        # symlink, FIFO or device metadata while retaining identical bytes.
        # Only regular files (or archives with no Unix type bits) are accepted.
        if any(
            item.is_dir()
            or item.file_size > MAX_FILE_BYTES
            or item.flag_bits & 0x1
            or item.external_attr & 0x10  # DOS directory attribute
            or stat.S_IFMT((item.external_attr >> 16) & 0xFFFF)
            not in (0, stat.S_IFREG)
            for item in entries
        ):
            raise BundleError("candidate_unsafe_zip_entry")
        if sum(item.file_size for item in entries) > MAX_TOTAL_BYTES:
            raise BundleError("candidate_zip_total_exceeded")
        manifest_raw = z.read("manifest.json")
        if hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256:
            raise BundleError("candidate_manifest_hash_mismatch")
        try:
            manifest = json.loads(manifest_raw)
        except ValueError as exc:
            raise BundleError("candidate_manifest_not_json") from exc
        if (not isinstance(manifest, dict) or
            manifest.get("product") != PRODUCT or
            manifest.get("schema_version") != SCHEMA or
            manifest.get("status") != "candidate_requires_independent_source_review"):
            raise BundleError("candidate_schema_mismatch")
        listed = manifest.get("files")
        if not isinstance(listed, list) or len(listed) != len(SAFE_NAMES):
            raise BundleError("candidate_manifest_invalid_inventory")
        indexed = {}
        for item in listed:
            if not isinstance(item, dict) or item.get("path") not in SAFE_NAMES:
                raise BundleError("candidate_manifest_extra_file")
            name = item["path"]
            if name in indexed:
                raise BundleError("candidate_manifest_duplicate_file")
            indexed[name] = item
        if set(indexed) != SAFE_NAMES:
            raise BundleError("candidate_manifest_missing_source")
        for name in SAFE_NAMES:
            data = z.read(name)
            item = indexed[name]
            if item.get("size") != len(data) or item.get("sha256") != hashlib.sha256(data).hexdigest():
                raise BundleError("candidate_source_hash_mismatch")
    return {"ok": True, "files_verified": len(SAFE_NAMES),
            "approved_for_public_release": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("audit", "build"))
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--version", default="v0.0.0-candidate")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "audit":
            manifest, _ = manifest_for(args.source, args.version)
            result = {"ok": True, "status": "review_required",
                      "files": len(manifest["files"]),
                      "sha256": hashlib.sha256(_manifest_bytes(manifest)).hexdigest()}
        else:
            if args.output is None:
                raise BundleError("output_required")
            result = build_local_candidate(args.source, args.output, args.version,
                                           explicit_local_only=args.local_only)
        print(json.dumps(result))
        return 0
    except (BundleError, OSError, ValueError, zipfile.BadZipFile):
        print(json.dumps({"ok": False, "error": "runtime_bundle_candidate_blocked"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
