"""Build a self-contained *GUI-only* Hermes A4 release candidate.

It never installs, activates, restarts services or sets approval flags.
The owner must independently authenticate the manifest SHA before using it.
Runtime modules and pinned Hermes core installer remain in original checkout.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .release_manager import ReleaseError, VERSION, _ensure_dir

ROOT=Path(__file__).resolve().parents[2]
SOURCE_FILES=("Setup-Hermes-A4.cmd","quickstart/a4/setup.ps1")


def build(version: str,destination: Path) -> dict:
    if not isinstance(version,str) or not VERSION.fullmatch(version):
        raise ReleaseError("invalid_release_version")
    dest=Path(destination).absolute()
    parent=_ensure_dir(dest.parent)
    if dest.exists() or dest.is_symlink():
        raise ReleaseError("destination_must_not_exist")
    # Never build directly into a live per-user release store.
    forbidden=(Path(os.environ.get("LOCALAPPDATA",""))/"Hermes-OneClick").absolute()
    if dest.is_relative_to(forbidden):
        raise ReleaseError("active_release_store_forbidden")
    stage=Path(tempfile.mkdtemp(prefix=".a4-gui-bundle-",dir=parent))
    try:
        entries=[]
        for relative in SOURCE_FILES:
            src=ROOT/relative
            if src.is_symlink() or not src.is_file() or (
                    not src.resolve(strict=True).is_relative_to(ROOT)):
                raise ReleaseError("untrusted_gui_source")
            raw=src.read_bytes()
            target=stage/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("xb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            entries.append({"path":relative,
                            "sha256":hashlib.sha256(raw).hexdigest()})
        manifest={"schema_version":1,"product":"Hermes-A4",
                  "version":version,"files":entries}
        raw=json.dumps(manifest,sort_keys=True,separators=(",",":")).encode("utf8")
        with (stage/"manifest.json").open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(stage,dest)
        return {"ok":True,"version":version,"bundle":str(dest),
                "manifest_sha256":hashlib.sha256(raw).hexdigest(),
                "files":len(entries),"activated":False,
                "note":"Candidate only; independent owner pin required"}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--version",required=True)
    parser.add_argument("--destination",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(build(args.version,args.destination),ensure_ascii=False))
        return 0
    except (ReleaseError,OSError,ValueError):
        print(json.dumps({"ok":False,"error":"release_bundle_build_blocked"}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
