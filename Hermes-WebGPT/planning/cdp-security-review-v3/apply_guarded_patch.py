"""SHA-pinned, staging-only installation of the CDP fixture Relay proof.

Keep this bundle OUTSIDE the target directory. Pass the absolute path to
Hermes-WebGPT/planning/cdp-live-preflight-20261111/standalone-worker-v2/ .

No production Gateway/Relay or browser process is ever touched. Default is
read-only --plan. --apply may change ONLY two pinned fixture-source files and
create the two new guard files, provided the original SHAs match exactly.
For normal errors, perform compare-before-restore rollback of OUR OWN bytes.
Power-loss and concurrent unauthorized filesystem writers are not solved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import secrets
from pathlib import Path
import sys
import tempfile

CURRENT_SHA = {
    "fixture_worker.py": "33f0b16ffedac9d15e3d88963ace48ffcccdcd6325a39984932103ccb9125f79",
    "test_worker_subprocess_e2e.py": "c2f30b852597ce0693d5d54b51d69942af04d2319b8376a46cb75aaebf6a6bad",
}

# Additional SHA guard for new payloads. This prevents accidentally applying
# a modified or stale archive even when the target source hashes still match.
BUNDLE_SHA = {
    "fixture_relay_attestation.py": "f50200ef447165a49aa95cf2dcf86fef6dbb207efc7fabd101827749b7371dc6",
    "test_fixture_relay_attestation.py": "2b140a56be6b6063e9a6da0d2993db5145ccab58973a0869ad561528f2579442",
}

# The source files were verified before the Local Workspace session ended.
# Exact source SHA & single-occurrence anchors prevent patching a drifted tree.
PATCHES = {
    "fixture_worker.py": [
        (
            "from playwright.async_api import async_playwright\n",
            "from playwright.async_api import async_playwright\n"
            "from fixture_relay_attestation import require_fixture_relay_attestation\n",
        ),
        (
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n",
            "    http=ScopedWorkerHttp(relay,key)\n"
            "    # This attestation is required BEFORE any /bridge/claim. A running\n"
            "    # production Relay does not expose the fixture proof endpoint.\n"
            "    proof_key = os.environ.get('HERMES_CDP_FIXTURE_PROOF_KEY', '')\n"
            "    if proof_key == key:\n"
            "        raise WorkerFault('fixture_proof_key_must_be_independent')\n"
            "    require_fixture_relay_attestation(relay, proof_key)\n"
            "    return await execute_one(http,args.fixture,args.case,journal)\n",
        ),
    ],
    "test_worker_subprocess_e2e.py": [
        (
            "import relay\nfrom cdp_fixture_server import create_server\n",
            "import relay\nfrom fixture_relay_attestation import attested_handler\n"
            "from cdp_fixture_server import create_server\n",
        ),
        (
            "        server.daemon_threads=True\n        server_thread=run_server(server)\n",
            "        # Fixture proof key is independent from the scoped worker bearer.\n"
            "        # Only this ephemeral QA Relay exposes the proof endpoint.\n"
            "        fixture_proof=secrets.token_urlsafe(40)\n"
            "        server.RequestHandlerClass=attested_handler(\n"
            "            server.RequestHandlerClass,fixture_proof)\n"
            "        server.daemon_threads=True\n        server_thread=run_server(server)\n",
        ),
        (
            '                env["HERMES_CDP_FIXTURE_WORKER_KEY"]=worker_auth\n',
            '                env["HERMES_CDP_FIXTURE_WORKER_KEY"]=worker_auth\n'
            '                env["HERMES_CDP_FIXTURE_PROOF_KEY"]=fixture_proof\n',
        ),
    ],
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(path: str, original: bytes) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError("source_not_utf8") from exc
    for old, new in PATCHES[path]:
        if text.count(old) != 1:
            raise RuntimeError("patch_anchor_missing_or_duplicated:" + path)
        text = text.replace(old, new, 1)
    compile(text, path, "exec")
    return text.encode("utf-8")


def preflight(target: Path, bundle: Path):
    if not target.is_absolute() or ".." in target.parts or not target.is_dir():
        raise RuntimeError("existing_absolute_target_required")
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise RuntimeError("linked_target_forbidden")
    if target.name != "standalone-worker-v2" or target.parent.name != "cdp-live-preflight-20261111":
        raise RuntimeError("wrong_staging_target")
    changed = {}
    for name, expected in CURRENT_SHA.items():
        p = target / name
        if p.is_symlink() or not p.is_file():
            raise RuntimeError("source_not_regular:" + name)
        raw = p.read_bytes()
        if sha(raw) != expected:
            raise RuntimeError("source_sha_drift:" + name)
        changed[name] = (raw, transform(name, raw))
    for name, (raw, _) in changed.items():
        backup = target / (name + ".pre-fixture-proof." + sha(raw)[:12] + ".bak")
        if backup.exists() or backup.is_symlink():
            raise RuntimeError("source_backup_already_exists:" + name)
    added = {}
    for name in ("fixture_relay_attestation.py", "test_fixture_relay_attestation.py"):
        source = bundle / name
        dest = target / name
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("bundle_source_missing_or_linked:" + name)
        if dest.exists() or dest.is_symlink():
            raise RuntimeError("target_new_file_already_exists:" + name)
        raw = source.read_bytes()
        if sha(raw) != BUNDLE_SHA[name]:
            raise RuntimeError("bundle_source_sha_drift:" + name)
        compile(raw, name, "exec")
        added[name] = raw
    return changed, added


def verify_post_commit(target: Path, changed: dict, added: dict) -> None:
    """Fail closed if a concurrent write corrupts any just-installed payload.

    This does not turn a multi-file write into an atomic transaction. It only
    prevents reporting success for bytes that differ from this reviewed patch.
    """
    expected = {name: after for name, (_before, after) in changed.items()}
    expected.update(added)
    for name, data in expected.items():
        final = target / name
        if final.is_symlink() or not final.is_file():
            raise RuntimeError("post_commit_file_not_regular:" + name)
        if sha(final.read_bytes()) != sha(data):
            raise RuntimeError("post_commit_digest_mismatch:" + name)



def _exclusive_publish(target: Path, name: str, raw: bytes) -> None:
    """Publish fully written bytes without exposing a partially written path.

    A hard link is an atomic CREATE-ONLY operation on the same filesystem.
    If Windows or the volume does not support this primitive, fail CLOSED:
    never degrade to non-atomic open("xb") + write for source publishing.
    """
    dest=target/name
    if dest.exists() or dest.is_symlink():
        raise RuntimeError("exclusive_destination_exists:"+name)
    fd,tmpname=tempfile.mkstemp(prefix=".cdp-proof-publish-",suffix=".tmp",dir=target)
    staged=Path(tmpname)
    linked=False
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.link(staged,dest)
        linked=True
        if dest.is_symlink() or not dest.is_file() or sha(dest.read_bytes()) != sha(raw):
            raise RuntimeError("exclusive_publish_integrity_failed:"+name)
    except Exception:
        # A monkeypatched/abnormal filesystem primitive may raise after a
        # side effect. Only delete our exact content, never a foreign change.
        if (dest.is_file() and not dest.is_symlink() and
                sha(dest.read_bytes()) == sha(raw)):
            dest.unlink()
        raise
    finally:
        if staged.exists():
            staged.unlink()

def _atomic_restore(target: Path, name: str, original: bytes, installed: bytes) -> bool:
    """Best-effort compensation ONLY when our exact installed bytes remain.

    Never overwrite a different/foreign author's version. A crash between
    comparison and replace is a documented limit of this filesystem approach.
    """
    p = target / name
    if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != sha(installed):
        return False
    fd, tmpname = tempfile.mkstemp(prefix=".cdp-proof-rollback-", suffix=".tmp", dir=target)
    staged = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(original)
            f.flush()
            os.fsync(f.fileno())
        # The comparison just above is intentionally repeated immediately
        # before replace, but a hostile process can still race this boundary.
        if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != sha(installed):
            return False
        os.replace(staged, p)
        return sha(p.read_bytes()) == sha(original)
    finally:
        if staged.exists():
            staged.unlink()


def _rollback_own_changes(target: Path, changed: dict, added: dict,
                          replaced: list[str], created: list[str]) -> dict:
    """Compensate successful mutations only when the exact own bytes remain.

    If another writer touched a file, leave it unchanged and report a conflict.
    Source backups are kept deliberately for owner inspection.
    """
    restored, removed, conflicts = [], [], []
    for name in reversed(replaced):
        before, after = changed[name]
        try:
            if _atomic_restore(target, name, before, after):
                restored.append(name)
            else:
                conflicts.append(name)
        except (OSError, RuntimeError):
            conflicts.append(name)
    for name in reversed(created):
        p = target / name
        try:
            if p.is_file() and not p.is_symlink() and sha(p.read_bytes()) == sha(added[name]):
                p.unlink()
                removed.append(name)
            else:
                conflicts.append(name)
        except (OSError, RuntimeError):
            conflicts.append(name)
    return {"restored":restored,"removed":removed,"conflicts":conflicts}


def _own_lock(target: Path) -> tuple[Path, bytes, int]:
    """Reserve only this install operation; preserve any foreign lock file."""
    lock = target / ".cdp_fixture_proof_patch.lock"
    marker = secrets.token_hex(24).encode("ascii")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("another_patch_or_stale_patch_lock") from exc
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(marker)
            f.flush()
            os.fsync(f.fileno())
        inode = lock.stat(follow_symlinks=False).st_ino
        return lock, marker, inode
    except Exception:
        # If the file was modified after creation, fail closed by preserving it.
        if lock.is_file() and not lock.is_symlink() and lock.read_bytes() == marker:
            lock.unlink()
        raise


def _lock_still_owned(lock: Path, marker: bytes, inode: int) -> bool:
    try:
        info = lock.stat(follow_symlinks=False)
        return (stat.S_ISREG(info.st_mode) and info.st_ino == inode and
                lock.read_bytes() == marker)
    except OSError:
        return False


def _release_own_lock(lock: Path, marker: bytes, inode: int) -> bool:
    if not _lock_still_owned(lock, marker, inode):
        return False
    try:
        lock.unlink()
        return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, required=True,
                    help="Existing Hermes standalone-worker-v2 directory")
    ap.add_argument("--apply", action="store_true",
                    help="Explicitly stage source-only fixture security patch")
    opts = ap.parse_args(argv)
    target = opts.target
    bundle = Path(__file__).resolve().parent
    changed, added = preflight(target, bundle)
    # Resolve only after rejecting symlinked source paths and ancestors.
    target = target.resolve(strict=True)
    print(json.dumps({"mode":"staging_apply" if opts.apply else "read_only_plan",
                      "source_sha_match":True,
                      "modified_sources":[
                          {"file":k,"before":sha(v[0]),"after":sha(v[1])}
                          for k,v in changed.items()],
                      "new_files":[{"file":k,"sha256":sha(v)}
                                   for k,v in added.items()],
                      "no_production_runtime_changes":True},sort_keys=True),flush=True)
    if not opts.apply:
        return 0

    lock, marker, inode = _own_lock(target)
    staged = {}
    replaced = []
    created = []
    backups = []
    try:
        # Re-validate source once the staging lock is held. This does not
        # compel unrelated agents to respect the lock, so post checks matter.
        for name, (before, _after) in changed.items():
            p = target / name
            if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != sha(before):
                raise RuntimeError("source_changed_after_staging_lock:"+name)
        for name in added:
            p = target / name
            if p.exists() or p.is_symlink():
                raise RuntimeError("new_file_appeared_after_staging_lock:"+name)

        for name, (before, _) in changed.items():
            backup_name = name + ".pre-fixture-proof." + sha(before)[:12] + ".bak"
            _exclusive_publish(target,backup_name,before)
            backups.append(backup_name)
        # Stage the entire update prior to first replacement.
        for name, raw in [(k,v[1]) for k,v in changed.items()] + list(added.items()):
            fd, tmpname = tempfile.mkstemp(prefix=".cdp-proof-",suffix=".tmp",dir=target)
            with os.fdopen(fd,"wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            staged[name] = Path(tmpname)

        # Create new proof modules first. Running worker can't import these
        # until its own source is changed, so keep fixture_worker.py LAST.
        for name in added:
            _exclusive_publish(target,name,added[name])
            created.append(name)

        update_order = ["test_worker_subprocess_e2e.py", "fixture_worker.py"]
        if set(update_order) != set(changed):
            raise RuntimeError("unexpected_changed_file_set")
        for name in update_order:
            before, after = changed[name]
            p = target / name
            if p.is_symlink() or not p.is_file() or sha(p.read_bytes()) != sha(before):
                raise RuntimeError("source_changed_during_commit:"+name)
            os.replace(staged[name], p)
            replaced.append(name)
            if p.is_symlink() or sha(p.read_bytes()) != sha(after):
                raise RuntimeError("source_bad_immediately_after_commit:"+name)
        verify_post_commit(target,changed,added)
        if not _lock_still_owned(lock, marker, inode):
            raise RuntimeError("staging_lock_ownership_lost")
        print(json.dumps({"ok":True,"changed":replaced,"added":created,
                          "rollback_backups":backups,
                          "production_relay_or_gateway_restarted":False},sort_keys=True),flush=True)
        return 0
    except Exception as exc:
        # A filesystem call can report an error after making its change.
        # Include any unrecorded outputs that match OUR exact staged bytes.
        # Never classify foreign contents as our output and never overwrite it.
        for name, (_before, after) in changed.items():
            if name in replaced:continue
            p=target/name
            try:
                if p.is_file() and not p.is_symlink() and sha(p.read_bytes())==sha(after):
                    replaced.append(name)
            except OSError:pass
        for name, raw in added.items():
            if name in created:continue
            p=target/name
            try:
                if p.is_file() and not p.is_symlink() and sha(p.read_bytes())==sha(raw):
                    created.append(name)
            except OSError:pass
        rollback = _rollback_own_changes(target,changed,added,replaced,created)
        print(json.dumps({"ok":False,"reason":type(exc).__name__,
                          "rollback":rollback,
                          "backups_preserved":backups,
                          "manual_review_required":bool(rollback["conflicts"])},
                         sort_keys=True),flush=True)
        raise RuntimeError("staging_patch_aborted_after_checked_rollback") from exc
    finally:
        for p in staged.values():
            if p.exists() and not p.is_symlink():
                try:p.unlink()
                except OSError:pass
        if not _release_own_lock(lock,marker,inode):
            # A modified/replaced lock is NOT ours to remove.
            print(json.dumps({"warning":"staging_lock_not_owned_after_operation",
                              "manual_review_required":True}),flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(json.dumps({"ok":False,"reason":str(e)[:180]},sort_keys=True))
        sys.exit(2)
