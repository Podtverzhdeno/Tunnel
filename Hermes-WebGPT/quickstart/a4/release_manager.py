"""Hermes A4 offline release staging, atomic activation and safe rollback.

No network access, browser automation, service restart or secret handling.
Only a user-approved, SHA256-pinned local bundle can be activated. A new
release is placed under an isolated per-user releases directory. The source
checkout and active processes are never modified.

This is a foundation for complete OneClick updates, NOT a full runtime updater.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

VERSION = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9_-]{1,24})?$")
HASH = re.compile(r"^[0-9a-f]{64}$")
FILES_MAX = 100
MAX_BYTES = 25 * 1024 * 1024
ENTRY_MAX = 10 * 1024 * 1024
REQUIRED = frozenset({"Setup-Hermes-A4.cmd", "quickstart/a4/setup.ps1"})


class ReleaseError(RuntimeError):
    pass


def _ensure_dir(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ReleaseError("unsafe_release_directory")
    if hasattr(path, "is_junction") and path.is_junction():
        raise ReleaseError("release_directory_junction_denied")
    return path.resolve(strict=True)


def _manifest(bundle: Path, digest: str) -> tuple[dict, list[tuple[str,str]]]:
    if not isinstance(digest, str) or not HASH.fullmatch(digest):
        raise ReleaseError("expected_trusted_manifest_sha256_required")
    bundle = _ensure_dir(bundle)
    path = bundle / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("manifest_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ReleaseError("manifest_hash_mismatch")
    try:
        manifest = json.loads(data)
    except ValueError as exc:
        raise ReleaseError("manifest_not_json") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or (
        manifest.get("product") != "Hermes-A4"):
        raise ReleaseError("invalid_release_schema")
    if not isinstance(manifest.get("version"), str) or not VERSION.fullmatch(
        manifest["version"]):
        raise ReleaseError("invalid_release_version")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 2 <= len(entries) <= FILES_MAX:
        raise ReleaseError("invalid_release_manifest_files")
    seen = set()
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReleaseError("invalid_manifest_entry")
        rel, sha = entry.get("path"), entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(sha, str) or not HASH.fullmatch(sha):
            raise ReleaseError("invalid_manifest_entry")
        if "\\" in rel or "\x00" in rel or ":" in rel:
            raise ReleaseError("unsafe_release_path")
        posix = PurePosixPath(rel)
        if posix.is_absolute() or rel != str(posix) or ".." in posix.parts:
            raise ReleaseError("unsafe_release_path")
        if rel != "Setup-Hermes-A4.cmd" and not (
            rel.startswith("quickstart/a4/") and
            rel.endswith((".py", ".ps1", ".md", ".cmd", ".txt"))
        ):
            raise ReleaseError("release_path_not_allowlisted")
        if rel.casefold() in seen:
            raise ReleaseError("duplicate_release_path")
        seen.add(rel.casefold())
        parsed.append((rel, sha))
    if not REQUIRED.issubset({x[0] for x in parsed}):
        raise ReleaseError("required_setup_files_missing")
    return manifest, parsed


class ReleaseManager:
    def __init__(self, state: Path):
        self.state = _ensure_dir(Path(state))
        self.releases = self.state / "a4-releases"
        if self.releases.exists() or self.releases.is_symlink():
            _ensure_dir(self.releases)
        # Constructing ReleaseManager and requesting "status" are strictly
        # read-only. The releases directory is created only by install().
        self.pointer = self.state / "a4-active-release.json"
        self.previous = self.state / "a4-active-release.previous.json"
        self.rollback_copy = self.state / "a4-active-release.rollback-current.json"
        self.lock = self.state / "a4-update.lock"

    def _lock(self):
        try:
            with self.lock.open("x", encoding="utf8") as out:
                out.write("update_in_progress\n")
                out.flush()
                os.fsync(out.fileno())
        except FileExistsError as exc:
            raise ReleaseError("update_busy_or_interrupted_requires_reconciliation") from exc

    def _verify_staged_release(self, stage: Path, version: str, digest: str):
        """Read-only integrity gate before reporting/activating a release.

        The pointer is an assertion, not evidence of intact files. Validate
        manifest, exact file inventory, hashes, directory boundaries and size
        limits on every status and rollback decision. Never execute a file.
        """
        root = _ensure_dir(stage)
        manifest, entries = _manifest(root, digest)
        if manifest["version"] != version:
            raise ReleaseError("release_integrity_version_mismatch")
        expected = dict(entries)
        expected["manifest.json"] = digest
        observed = set()
        total_bytes = 0
        nodes = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            _ensure_dir(directory)
            for child in directory.iterdir():
                nodes += 1
                if nodes > FILES_MAX * 8:
                    raise ReleaseError("release_integrity_tree_excess")
                if child.is_symlink() or (hasattr(child, "is_junction")
                                          and child.is_junction()):
                    raise ReleaseError("release_integrity_reparse_denied")
                if not child.resolve(strict=True).is_relative_to(root):
                    raise ReleaseError("release_integrity_path_escape")
                if child.is_dir():
                    pending.append(child)
                    continue
                relative = child.relative_to(root).as_posix()
                if (not child.is_file() or relative not in expected
                        or relative in observed):
                    raise ReleaseError("release_integrity_extra_file")
                observed.add(relative)
                size = child.stat().st_size
                total_bytes += size
                if size > ENTRY_MAX or total_bytes > MAX_BYTES:
                    raise ReleaseError("release_integrity_size_exceeded")
                with child.open("rb") as inp:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: inp.read(64 * 1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest() != expected[relative]:
                    raise ReleaseError("release_integrity_hash_mismatch")
        if observed != set(expected):
            raise ReleaseError("release_integrity_missing_file")

    def _read_pointer(self, path: Path) -> dict | None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ReleaseError("unsafe_release_pointer")
        if not path.exists():
            return None
        try:
            item=json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError) as exc:
            raise ReleaseError("invalid_release_pointer") from exc
        if (not isinstance(item, dict) or
            not isinstance(item.get("version"), str) or
            not VERSION.fullmatch(item["version"]) or
            not isinstance(item.get("manifest_sha256"), str) or
            not HASH.fullmatch(item["manifest_sha256"])):
            raise ReleaseError("invalid_release_pointer")
        stage = self.releases / item["version"]
        if not stage.is_dir() or stage.is_symlink():
            raise ReleaseError("release_pointer_target_missing")
        self._verify_staged_release(stage, item["version"],
                                    item["manifest_sha256"])
        return item

    def active(self) -> dict | None:
        return self._read_pointer(self.pointer)

    def resolve_launch(self) -> dict:
        """Select only a revalidated staged GUI, never an unverified pointer."""
        active = self.active()
        if active is None:
            return {"ok": True, "release_active": False}
        stage = _ensure_dir(self.releases / active["version"])
        script = stage / "quickstart" / "a4" / "setup.ps1"
        if script.is_symlink() or not script.is_file():
            raise ReleaseError("release_integrity_gui_missing")
        return {"ok": True, "release_active": True,
                "version": active["version"], "manifest_sha256": active["manifest_sha256"],
                "release_root": str(stage), "script": str(script)}

    def _write_atomic(self, path: Path, data: dict):
        if path.is_symlink():
            raise ReleaseError("unsafe_release_pointer")
        temp = path.with_name(path.name + ".new")
        if temp.exists() or temp.is_symlink():
            raise ReleaseError("stale_release_temp_requires_reconciliation")
        try:
            with temp.open("x", encoding="utf8") as out:
                json.dump(data, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def preview(self, bundle: Path, expected_manifest_sha256: str) -> dict:
        """Read-only verification of a selected local release before consent.

        Preview is never an authorization grant. Install rechecks all hashes
        while copying after the user explicitly approves the update.
        """
        manifest, entries = _manifest(Path(bundle), expected_manifest_sha256)
        root = _ensure_dir(Path(bundle))
        total = 0
        for rel, expected in entries:
            file = root / rel
            if file.is_symlink() or not file.is_file() or (
                not file.resolve(strict=True).is_relative_to(root)):
                raise ReleaseError("unsafe_bundle_file")
            size = file.stat().st_size
            total += size
            if size > ENTRY_MAX or total > MAX_BYTES:
                raise ReleaseError("bundle_too_large")
            hasher = hashlib.sha256()
            with file.open("rb") as inp:
                for chunk in iter(lambda: inp.read(64 * 1024), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != expected:
                raise ReleaseError("bundle_file_hash_mismatch")
        return {"ok": True, "state": "preview_only",
                "version": manifest["version"], "files_verified": len(entries),
                "manifest_sha256": expected_manifest_sha256}

    def install(self, bundle: Path, expected_manifest_sha256: str) -> dict:
        # Owner approval must be handled before calling install().
        manifest, entries = _manifest(Path(bundle), expected_manifest_sha256)
        version = manifest["version"]
        bundle = _ensure_dir(Path(bundle))
        self._lock()
        try:
            self.releases.mkdir(exist_ok=True)
            _ensure_dir(self.releases)
            current = self.active()
            if current and current["version"] == version:
                if current["manifest_sha256"] != expected_manifest_sha256:
                    raise ReleaseError("version_pin_conflict")
                return {"ok": True, "state": "already_active", "version": version}
            if self.previous.exists() or self.rollback_copy.exists():
                raise ReleaseError("previous_rollback_requires_reconciliation")
            target = self.releases / version
            if target.exists() or target.is_symlink():
                raise ReleaseError("release_version_already_staged")
            staged = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.releases))
            try:
                total = 0
                for rel, expected in entries:
                    original = bundle / rel
                    if original.is_symlink() or not original.is_file() or (
                        not original.resolve(strict=True).is_relative_to(bundle)):
                        raise ReleaseError("unsafe_bundle_file")
                    size = original.stat().st_size
                    total += size
                    if size > ENTRY_MAX or total > MAX_BYTES:
                        raise ReleaseError("bundle_too_large")
                    dest = staged / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    digest=hashlib.sha256()
                    with original.open("rb") as inp, dest.open("xb") as out:
                        while chunk := inp.read(64 * 1024):
                            digest.update(chunk)
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                    if digest.hexdigest() != expected:
                        raise ReleaseError("bundle_file_hash_mismatch")
                shutil.copyfile(bundle/"manifest.json", staged/"manifest.json")
                # The source manifest can change after the initial hash
                # check. Verify the *staged* manifest and payload before
                # moving this version into the store or changing any pointer.
                # A rejected stage is deleted; the last active release stays
                # available and no rollback marker is written.
                self._verify_staged_release(staged, version, expected_manifest_sha256)
                os.replace(staged, target)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
            new={"version":version, "manifest_sha256":expected_manifest_sha256}
            if current:
                self._write_atomic(self.previous, current)
            self._write_atomic(self.pointer, new)
            return {"ok": True, "state": "activated", "version": version,
                    "rollback_available": current is not None}
        finally:
            self.lock.unlink(missing_ok=True)

    def resume_activation(self, version: str, expected_manifest_sha256: str) -> dict:
        """Explicit owner reconciliation after an interrupted staged update.

        Never auto-retry a release change after an uncertain acknowledgement.
        The owner pins the exact staged version and manifest digest again.
        """
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ReleaseError("invalid_release_version")
        if (not isinstance(expected_manifest_sha256, str) or
                not HASH.fullmatch(expected_manifest_sha256)):
            raise ReleaseError("expected_trusted_manifest_sha256_required")
        self._lock()
        try:
            if self.rollback_copy.exists() or self.rollback_copy.is_symlink():
                raise ReleaseError("rollback_reconciliation_required_first")
            staged = self.releases / version
            self._verify_staged_release(staged, version, expected_manifest_sha256)
            current = self.active()
            wanted = {"version": version,
                      "manifest_sha256": expected_manifest_sha256}
            if current == wanted:
                return {"ok": True, "state": "already_active", "version": version}
            previous = self._read_pointer(self.previous)
            if previous is not None:
                if previous != current:
                    raise ReleaseError("interrupted_activation_pointer_mismatch")
            elif current is not None:
                # Failure may have happened before the backup was recorded.
                self._write_atomic(self.previous, current)
            self._write_atomic(self.pointer, wanted)
            return {"ok": True, "state": "resumed_activation",
                    "version": version, "rollback_available": current is not None}
        finally:
            self.lock.unlink(missing_ok=True)

    def resume_rollback(self) -> dict:
        """Explicit reconciliation of a partially written rollback pointer."""
        self._lock()
        try:
            current = self.active()
            previous = self._read_pointer(self.previous)
            displaced = self._read_pointer(self.rollback_copy)
            if current is None or previous is None or displaced is None:
                raise ReleaseError("no_incomplete_rollback_to_reconcile")
            if current == displaced:
                self._write_atomic(self.pointer, previous)
            elif current != previous:
                raise ReleaseError("interrupted_rollback_pointer_mismatch")
            self.previous.unlink()
            return {"ok": True, "state": "resumed_rollback",
                    "version": previous["version"]}
        finally:
            self.lock.unlink(missing_ok=True)

    def rollback(self) -> dict:
        self._lock()
        try:
            current=self.active()
            previous=self._read_pointer(self.previous)
            if current is None or previous is None or self.rollback_copy.exists():
                raise ReleaseError("rollback_unavailable")
            # Save displaced active version in a dedicated backup, then make
            # the previous version active. Do not delete any release bytes.
            self._write_atomic(self.rollback_copy, current)
            self._write_atomic(self.pointer, previous)
            self.previous.unlink()
            return {"ok": True, "state": "rolled_back",
                    "version": previous["version"]}
        finally:
            self.lock.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "resolve", "preview", "install", "rollback", "resume-activation", "resume-rollback"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--version")
    parser.add_argument("--approved", action="store_true")
    args=parser.parse_args(argv)
    try:
        mgr=ReleaseManager(args.state)
        if args.action=="status":
            result={"ok": True,"active": mgr.active()}
        elif args.action=="resolve":
            result=mgr.resolve_launch()
        elif args.action=="preview":
            if args.bundle is None:
                raise ReleaseError("bundle_required")
            result=mgr.preview(args.bundle, args.manifest_sha256)
        elif not args.approved:
            raise ReleaseError("explicit_user_approval_required")
        elif args.action=="install":
            if args.bundle is None:
                raise ReleaseError("bundle_required")
            result=mgr.install(args.bundle, args.manifest_sha256)
        elif args.action=="resume-activation":
            result=mgr.resume_activation(args.version, args.manifest_sha256)
        elif args.action=="resume-rollback":
            result=mgr.resume_rollback()
        else:
            result=mgr.rollback()
        print(json.dumps(result))
        return 0
    except (ReleaseError, OSError, ValueError, TypeError):
        print(json.dumps({"ok":False, "error":"release_operation_blocked"}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
