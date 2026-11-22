#!/usr/bin/env python3
"""Strict, offline, read-only source-integrity gate for the Hermes WebGPT MV3 pair.

It reads seven public extension files in two Git worktree paths. It does NOT
read local browser profiles, bridge-config.json, Telegram tokens, runtime data,
or ChatGPT conversations. It does not monitor a running browser. Not a rollout
authority, noReplay grant, or approval mechanism.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import subprocess

PAIR = (
    "manifest.json", "core.js", "content.js", "background.js",
    "background_content.js", "popup.html", "popup.js",
)
BASE = Path("chat-launch-smoke-desktop-test")
SOURCE = BASE / "web-companion"
COPY = BASE / "web-extension"
VERSION = "0.6.6"  # Pin reviewed contract; bump only with an explicit review.
HOSTS = ("http://127.0.0.1:18763/*", "https://chatgpt.com/*")
PERMS = ("alarms", "tabs", "storage", "scripting")
SCRIPTS = ("core.js", "content.js", "background_content.js")
MAX_FILE_BYTES = 5 * 1024 * 1024
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class EvidenceUnavailable(RuntimeError):
    """The scan could not securely inspect required files."""


class UnsafeContract(ValueError):
    """The mirrored files exist but violate a reviewed security contract."""


def _canonical(item: object) -> bytes:
    return json.dumps(item, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def _read_guarded(path: Path, root: Path) -> bytes:
    """Reject symlinks at every child path component, even if in a directory."""
    if not path.is_relative_to(root):
        raise EvidenceUnavailable("EVIDENCE_PATH_OUTSIDE_ROOT")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceUnavailable("EVIDENCE_SYMLINK")
    if not path.is_file():
        raise EvidenceUnavailable("EVIDENCE_FILE_MISSING")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise EvidenceUnavailable("EVIDENCE_READ_FAILED") from None
    if len(data) > MAX_FILE_BYTES:
        raise EvidenceUnavailable("EVIDENCE_FILE_TOO_LARGE")
    return data


def _manifest_contract(data: bytes) -> dict:
    try:
        m = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise UnsafeContract("MANIFEST_JSON_INVALID") from None
    if not isinstance(m, dict):
        raise UnsafeContract("MANIFEST_NOT_OBJECT")
    if set(m) != {"manifest_version", "name", "version", "description",
                  "permissions", "host_permissions", "background", "action",
                  "content_scripts"}:
        raise UnsafeContract("MANIFEST_FIELDS_CHANGED")
    if type(m.get("manifest_version")) is not int or m["manifest_version"] != 3:
        raise UnsafeContract("MANIFEST_VERSION_UNEXPECTED")
    if m.get("version") != VERSION:
        raise UnsafeContract("EXTENSION_VERSION_REVIEW_REQUIRED")
    if m.get("permissions") != list(PERMS):
        raise UnsafeContract("PERMISSION_SET_CHANGED")
    if m.get("host_permissions") != list(HOSTS):
        raise UnsafeContract("HOST_SCOPE_CHANGED")
    bg = m.get("background")
    if bg != {"service_worker": "background.js"}:
        raise UnsafeContract("BACKGROUND_WORKER_CHANGED")
    action = m.get("action")
    if (not isinstance(action, dict) or
        action.get("default_popup") != "popup.html" or
        set(action) != {"default_popup", "default_title"}):
        raise UnsafeContract("ACTION_POPUP_CHANGED")
    cs = m.get("content_scripts")
    if (not isinstance(cs, list) or len(cs) != 1 or
        not isinstance(cs[0], dict) or
        set(cs[0]) != {"matches", "js", "run_at"} or
        cs[0].get("matches") != ["https://chatgpt.com/*"] or
        cs[0].get("js") != list(SCRIPTS) or
        cs[0].get("run_at") != "document_idle"):
        raise UnsafeContract("CONTENT_SCRIPT_SCOPE_CHANGED")
    return {"manifest_version": 3, "extension_version": VERSION,
            "permissions": list(PERMS), "host_permissions": list(HOSTS)}


def _validate_revision(root: Path, revision: str | None) -> bool:
    """Bind a supplied SHA to a clean, tracked checkout; never trust user text."""
    if revision is None:
        return False
    if not SHA_PATTERN.fullmatch(revision):
        raise EvidenceUnavailable("INVALID_SOURCE_REVISION")
    # Git pathspec and ls-files --zero consistently use POSIX separators, even on Windows.
    paths = [(folder / name).as_posix() for folder in (SOURCE, COPY) for name in PAIR]

    def run_git(*args: str, text: bool = True):
        try:
            cmd = ["git", "-C", str(root), *args]
            return subprocess.run(cmd, capture_output=True, text=text,
                                  timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise EvidenceUnavailable("SOURCE_REVISION_UNVERIFIABLE") from None

    head = run_git("rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != revision:
        raise EvidenceUnavailable("SOURCE_REVISION_NOT_CHECKED_OUT")
    tracked = run_git("ls-files", "-z", "--", *paths, text=False)
    if tracked.returncode != 0:
        raise EvidenceUnavailable("SOURCE_FILES_UNTRACKED_OR_UNKNOWN")
    tracked_paths = {p.decode("utf-8", errors="replace")
                     for p in tracked.stdout.split(b"\0") if p}
    if tracked_paths != set(paths):
        raise EvidenceUnavailable("SOURCE_FILES_UNTRACKED_OR_UNKNOWN")
    dirty = run_git("diff", "--quiet", "HEAD", "--", *paths)
    if dirty.returncode != 0:
        raise EvidenceUnavailable("SOURCE_FILES_DIRTY_OR_UNKNOWN")
    return True


def inspect(root: Path, revision: str | None = None) -> dict:
    """Produce only public source hashes, not content, runtime status or secrets."""
    root = root.resolve()
    if revision is not None and not SHA_PATTERN.fullmatch(revision):
        raise EvidenceUnavailable("INVALID_SOURCE_REVISION")
    digest_rows = []
    manifest = None
    for filename in PAIR:
        original = _read_guarded(root / SOURCE / filename, root)
        mirror = _read_guarded(root / COPY / filename, root)
        first_sha = hashlib.sha256(original).hexdigest()
        second_sha = hashlib.sha256(mirror).hexdigest()
        if original != mirror:
            raise UnsafeContract("MIRROR_CONTENT_DIVERGED:" + filename)
        digest_rows.append({"file": filename, "sha256": first_sha,
                            "bytes": len(original)})
        if filename == "manifest.json":
            manifest = _manifest_contract(original)
    revision_verified = _validate_revision(root, revision)
    return {"schema_version": 1, "status": "source_pair_verified",
            "source_revision": revision, "source_revision_verified": revision_verified, "files_verified": len(PAIR),
            "source": str(SOURCE), "mirror": str(COPY),
            "manifest": manifest,
            "file_digests": digest_rows,
            "evidence_sha256": hashlib.sha256(_canonical(digest_rows)).hexdigest(),
            "runtime_verified": False,
            "external_effects_verified": False,
            "no_replay_authority": False,
            "note": "Static repository files only; not proof of installed extension, loaded runtime, ChatGPT send or Telegram delivery."}


def _incident(exc: Exception, revision: str | None) -> dict:
    # Never include user-provided paths, raw file bytes, exception objects or URLs.
    if isinstance(exc, UnsafeContract):
        text = str(exc)
        if text.startswith("MIRROR_CONTENT_DIVERGED:"):
            reason, name = text.split(":", 1)
            if name not in PAIR:
                name = "unknown"
        else:
            reason, name = text, None
        status = "blocked"
    elif isinstance(exc, EvidenceUnavailable):
        reason, name, status = str(exc), None, "evidence_unknown"
    else:
        reason, name, status = "UNEXPECTED_AUDIT_FAILURE", None, "evidence_unknown"
    return {"schema_version": 1, "status": status,
            "reason_code": reason, "file": name,
            "source_revision": revision if isinstance(revision, str) and SHA_PATTERN.fullmatch(revision) else None,
            "runtime_verified": False, "external_effects_verified": False,
            "no_replay_authority": False,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "safe_next_action": "Review source at exact revision; do not deploy or auto-replay browser actions."}


def _safe_report_target(root: Path, requested: Path) -> Path:
    """Never overwrite source, credentials or arbitrary files via --report."""
    # Preserve the caller's lexical checkout spelling: Windows 8.3 aliases
    # (RUNNER~1) can differ from Path.resolve() canonical long paths.
    # Symlink protection is enforced separately on the final path components.
    root = root.absolute()
    if root.is_symlink():
        raise EvidenceUnavailable("REPORT_SYMLINK")
    if requested.name not in {"mirror-report.json", "webgpt-mirror-report.json"}:
        raise EvidenceUnavailable("REPORT_FILENAME_NOT_ALLOWLISTED")
    if ".." in requested.parts:
        raise EvidenceUnavailable("REPORT_PATH_TRAVERSAL")
    target = requested if requested.is_absolute() else root / requested
    target = target.absolute()
    if not target.is_relative_to(root):
        raise EvidenceUnavailable("REPORT_OUTSIDE_CHECKOUT")
    relative = target.relative_to(root)
    forbidden = {".git", "bridge-state", "profiles", "web-companion", "web-extension"}
    if any(part in forbidden for part in relative.parts):
        raise EvidenceUnavailable("REPORT_PATH_PROTECTED")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise EvidenceUnavailable("REPORT_SYMLINK")
    if target.exists() and not target.is_file():
        raise EvidenceUnavailable("REPORT_NOT_FILE")
    return target


def _save_json(path: Path, result: dict) -> None:
    # Atomic report replace, not a Relay state transition or durable tool receipt.
    # Only the explicitly requested report path can be written.
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mirror-report-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(_canonical(result) + b"\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2],
                        help="Repository checkout root (read-only)")
    parser.add_argument("--revision", help="Exact verified Git SHA (optional)")
    parser.add_argument("--report", type=Path, help="Write a metadata-only JSON result")
    args = parser.parse_args(argv)
    try:
        result = inspect(args.root, args.revision)
        result["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
        exit_code = 0
    except (EvidenceUnavailable, UnsafeContract) as exc:
        result = _incident(exc, args.revision)
        exit_code = 3 if isinstance(exc, EvidenceUnavailable) else 2
    except Exception:
        result = _incident(RuntimeError("unexpected"), args.revision)
        exit_code = 3
    if args.report:
        try:
            target = _safe_report_target(args.root, args.report)
            _save_json(target, result)
        except EvidenceUnavailable as exc:
            print(f"EVIDENCE_UNKNOWN: {exc}", file=sys.stderr)
            return 3
        except OSError:
            print("EVIDENCE_UNKNOWN: REPORT_WRITE_FAILED", file=sys.stderr)
            return 3
    print(f"status={result['status']} reason={result.get('reason_code', 'none')} "
          f"files={result.get('files_verified', 0)}", file=sys.stderr if exit_code else sys.stdout)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
