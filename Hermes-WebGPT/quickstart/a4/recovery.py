"""A4 settings/DPAPI rollback support. Does not touch running services.

Backups remain under the Windows user's per-user config directory; snapshots
contain encrypted DPAPI bytes, never decrypted plaintext. No repository
browser state, credentials or process receipts are modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

NAMES = ("settings-a4.json", "telegram-token.dpapi.xml",
         "control-plane-key.dpapi.xml")


class RecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Recovery:
    state: Path

    def __post_init__(self):
        state = Path(self.state)
        if not state.is_dir() or state.is_symlink():
            raise RecoveryError("unsafe_state_directory")
        object.__setattr__(self, "state", state.resolve(strict=True))

    def _file(self, name: str, suffix: str = "") -> Path:
        if name not in NAMES or suffix not in ("", ".previous", ".rollback-current"):
            raise RecoveryError("unmanaged_recovery_path")
        file = self.state / (name + suffix)
        if file.is_symlink() or (file.exists() and not file.is_file()):
            raise RecoveryError("unsafe_managed_file")
        return file

    def present(self) -> dict[str, dict[str, bool]]:
        return {n: {"current": self._file(n).is_file(),
                    "previous": self._file(n, ".previous").is_file()} for n in NAMES}

    def backup(self, name: str) -> None:
        current = self._file(name)
        prev = self._file(name, ".previous")
        temp = self.state / (name + ".backup-new")
        if not current.is_file() or temp.exists() or temp.is_symlink():
            raise RecoveryError("backup_source_unavailable")
        with current.open("rb") as inp, temp.open("xb") as out:
            shutil.copyfileobj(inp, out, 64 * 1024)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.replace(temp, prev)
        finally:
            temp.unlink(missing_ok=True)

    def restore(self, name: str) -> None:
        """Explicit action only. Current version is retained as rollback-current."""
        previous = self._file(name, ".previous")
        current = self._file(name)
        rollback = self._file(name, ".rollback-current")
        if not previous.is_file() or rollback.exists():
            raise RecoveryError("restore_unavailable_or_conflicted")
        if current.is_file():
            # Preserve current bytes before replacing. Never mutate previous
            # until the local rollback copy has been made successfully.
            temp = self.state / (name + ".rollback-new")
            if temp.exists() or temp.is_symlink():
                raise RecoveryError("interrupted_rollback_requires_reconciliation")
            with current.open("rb") as inp, temp.open("xb") as out:
                shutil.copyfileobj(inp, out, 64 * 1024)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, rollback)
        os.replace(previous, current)


def main(argv=None):
    import argparse
    import json
    parser=argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "restore"))
    parser.add_argument("--name", choices=NAMES)
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ""))/"Hermes-OneClick")
    args=parser.parse_args(argv)
    try:
        r=Recovery(args.state_dir)
        if args.action=="plan":
            result={"ok":True,"items":r.present()}
        else:
            if args.name not in NAMES:
                raise RecoveryError("missing_managed_name")
            r.restore(args.name)
            result={"ok":True,"restored":args.name}
        print(json.dumps(result))
        return 0
    except (RecoveryError, OSError):
        print(json.dumps({"ok":False,"error":"recovery_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
