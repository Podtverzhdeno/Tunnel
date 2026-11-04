"""Durable, bounded merge intent for reviewer-controlled Python file integration.

Intent and backup bytes are fsynced BEFORE any project files are changed.
Recovery is an explicit, interactive local owner operation. A pending intent
blocks later merges; no unattended restore of potentially user-edited files.
This DOES NOT authenticate the caller as a reviewer at the MCP host.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

SAFE_TASK = re.compile(r"^P[0-9]{3}$")
SAFE_FILE = re.compile(r"^(?:[a-z][a-z0-9_]*|test_[a-z][a-z0-9_]*)\.py$")
MAX_FILE = 2 * 1024 * 1024


class MergeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _write_durable(path, contents):
    with path.open("xb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write(target, content):
    if target.is_symlink():
        raise MergeError("TARGET_CHANGED", "Refuse symlink target")
    fd, temporary = tempfile.mkstemp(prefix=".agent-review-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _existing(target):
    if target.is_symlink():
        raise MergeError("TARGET_CHANGED", "Symlink target is not allowed")
    if not target.exists():
        return None
    if not target.is_file() or target.stat().st_size > MAX_FILE:
        raise MergeError("TARGET_CHANGED", "Target is not a bounded regular file")
    return target.read_bytes()


class MergeIntent:
    def __init__(self, project, state, task_id):
        if not SAFE_TASK.fullmatch(task_id):
            raise MergeError("INVALID_TASK", "Invalid task identifier")
        self.project = Path(project).resolve()
        self.base = Path(state).resolve() / "merge-intents"
        self.folder = self.base / task_id
        self.manifest = self.folder / "intent.json"
        if not self.folder.is_relative_to(self.base):
            raise MergeError("PATH_DENIED", "Invalid intent directory")

    def pending(self):
        return self.folder.exists()

    def prepare(self, files, expected_base, revision):
        """files: list of (name, source Path); base hashes may be null."""
        if self.pending():
            raise MergeError("MERGE_PENDING", "Unresolved merge intent must be inspected")
        if not 1 <= len(files) <= 6 or len({name for name, _ in files}) != len(files):
            raise MergeError("INVALID_FILES", "Expected 1..6 unique deliverables")
        self.base.mkdir(parents=True, exist_ok=True)
        self.folder.mkdir(exist_ok=False)
        entries = []
        try:
            for index, (name, source) in enumerate(sorted(files)):
                if not SAFE_FILE.fullmatch(name) or name not in expected_base:
                    raise MergeError("UNSCOPED_FILE", "Only listed task deliverables may be merged")
                target = self.project / name
                before = _existing(target)
                source = Path(source)
                if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_FILE:
                    raise MergeError("SOURCE_CHANGED", "Untrusted source path or file size")
                after = source.read_bytes()
                old_sha = _sha(before) if before is not None else None
                if old_sha != expected_base[name]:
                    raise MergeError("TARGET_CHANGED", "Target changed since snapshot")
                before_name = f"{index}.old"
                after_name = f"{index}.new"
                if before is not None:
                    _write_durable(self.folder / before_name, before)
                _write_durable(self.folder / after_name, after)
                entries.append({
                    "name": name, "before_file": before_name if before is not None else None,
                    "after_file": after_name, "before_sha": old_sha, "after_sha": _sha(after),
                })
            doc = {"version": 1, "task_id": self.folder.name, "revision": revision,
                   "created_at_unix": time.time(), "entries": entries}
            _write_durable(self.manifest, json.dumps(doc,sort_keys=True).encode("utf-8"))
            return doc
        except BaseException:
            # Only safe before any target modifications: prepare never edits project.
            shutil.rmtree(self.folder, ignore_errors=True)
            raise

    def load(self):
        if not self.manifest.is_file() or self.manifest.is_symlink():
            raise MergeError("INTENT_INCOMPLETE", "Incomplete merge intent: manual inspection required")
        doc = json.loads(self.manifest.read_text(encoding="utf-8"))
        if (doc.get("version") != 1 or doc.get("task_id") != self.folder.name or
            not isinstance(doc.get("entries"), list) or not 1 <= len(doc["entries"]) <= 6):
            raise MergeError("INTENT_INVALID", "Intent manifest has invalid structure")
        for entry in doc["entries"]:
            if not SAFE_FILE.fullmatch(entry["name"]):
                raise MergeError("INTENT_INVALID", "Intent references unexpected file")
            for key, sha_key in (("before_file", "before_sha"), ("after_file", "after_sha")):
                fname = entry[key]
                sha = entry[sha_key]
                if fname is None:
                    if sha is not None:
                        raise MergeError("INTENT_INVALID", "Missing backup has unexpected hash")
                    continue
                if not re.fullmatch(r"[0-5]\.(old|new)", fname):
                    raise MergeError("INTENT_INVALID", "Invalid staged filename")
                p = self.folder / fname
                if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE:
                    raise MergeError("INTENT_CORRUPT", "Staged content missing or oversized")
                if _sha(p.read_bytes()) != sha:
                    raise MergeError("INTENT_CORRUPT", "Staged content hash mismatch")
        return doc

    def inspect(self):
        doc = self.load()
        files = {}
        for entry in doc["entries"]:
            current = _existing(self.project / entry["name"])
            actual = _sha(current) if current is not None else None
            if actual == entry["before_sha"] == entry["after_sha"]:
                state = "unchanged"
            elif actual == entry["after_sha"]:
                state = "applied"
            elif actual == entry["before_sha"]:
                state = "original"
            else:
                state = "diverged"
            files[entry["name"]] = {"state": state, "actual_sha": actual,
                                    "before_sha": entry["before_sha"],
                                    "after_sha": entry["after_sha"]}
        return {"task_id": self.folder.name, "files": files, "revision": doc["revision"],
                "diverged": any(v["state"]=="diverged" for v in files.values())}

    def apply(self, after_each=None):
        doc = self.load()
        for entry in doc["entries"]:
            target = self.project / entry["name"]
            current = _existing(target)
            current_hash = _sha(current) if current is not None else None
            if current_hash != entry["before_sha"]:
                raise MergeError("TARGET_CHANGED", "Target changed before integration")
            new_bytes = (self.folder / entry["after_file"]).read_bytes()
            _atomic_write(target, new_bytes)
            if after_each:
                after_each(entry["name"])
        if self.inspect()["diverged"]:
            raise MergeError("MERGE_CONFLICT", "Merged targets diverged")

    def rollback(self):
        """Explicit recovery operation: only original/applied hashes are permitted."""
        diagnosis = self.inspect()
        if diagnosis["diverged"]:
            raise MergeError("MERGE_CONFLICT", "User-modified target: refusing rollback")
        for entry in reversed(self.load()["entries"]):
            target = self.project / entry["name"]
            current = _existing(target)
            current_hash = _sha(current) if current is not None else None
            if current_hash == entry["before_sha"]:
                continue
            if current_hash != entry["after_sha"]:
                raise MergeError("MERGE_CONFLICT", "Changed while recovering")
            if entry["before_file"] is None:
                target.unlink()
            else:
                _atomic_write(target, (self.folder / entry["before_file"]).read_bytes())
        if any(x["state"] not in ("original","unchanged") for x in self.inspect()["files"].values()):
            raise MergeError("RECOVERY_INCOMPLETE", "Some original files were not restored")

    def cleanup(self):
        """Never cleanup before the DB commit or before explicit audited recovery."""
        if not self.folder.exists():
            return
        self.load()
        shutil.rmtree(self.folder)
