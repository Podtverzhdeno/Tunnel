"""P085 load owner-pinned canonical MergeIntent from protected installation.

At *build/install time* an authorized operator copies the independently
reviewed canonical review_merge.py to p085_trusted/review_merge.py, pins its
SHA-256 in the private protected config, and denies untrusted workers read or
write access to that installation. Never import code from the live mutable
project tree or from an agent's sandbox during privileged review.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

from .review_grants import GrantDenied

SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_JOURNAL_SOURCE = 128 * 1024


def source_sha(service_root: Path) -> str:
    root = Path(service_root).resolve(strict=True)
    folder = root / "p085_trusted"
    path = folder / "review_merge.py"
    if (folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
            or path.is_symlink() or getattr(path, "is_junction", lambda: False)()):
        raise GrantDenied("TRUSTED_JOURNAL_LINKED")
    if not folder.is_dir() or not path.is_file():
        raise GrantDenied("TRUSTED_JOURNAL_MISSING")
    if not path.resolve(strict=True).is_relative_to(root):
        raise GrantDenied("TRUSTED_JOURNAL_OUTSIDE_INSTALL")
    if path.stat().st_size > MAX_JOURNAL_SOURCE:
        raise GrantDenied("TRUSTED_JOURNAL_OVERSIZED")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JOURNAL_SOURCE+1)
    if len(raw)>MAX_JOURNAL_SOURCE:
        raise GrantDenied("TRUSTED_JOURNAL_OVERSIZED")
    return hashlib.sha256(raw).hexdigest()


def load_pinned_journal(service_root: Path, expected_sha256: str):
    if type(expected_sha256) is not str or not SHA.fullmatch(expected_sha256):
        raise GrantDenied("TRUSTED_JOURNAL_PIN_INVALID")
    actual = source_sha(service_root)
    if actual != expected_sha256:
        raise GrantDenied("TRUSTED_JOURNAL_PIN_MISMATCH")
    path=Path(service_root).resolve(strict=True)/"p085_trusted"/"review_merge.py"
    spec=importlib.util.spec_from_file_location(
        "_p085_owner_pinned_merge_"+expected_sha256[:16],path)
    if spec is None or spec.loader is None:
        raise GrantDenied("TRUSTED_JOURNAL_LOAD_UNAVAILABLE")
    mod=importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except (ImportError,RuntimeError,SyntaxError,ValueError) as exc:
        raise GrantDenied("TRUSTED_JOURNAL_LOAD_FAILED") from exc
    cls=getattr(mod,"MergeIntent",None)
    if (not isinstance(cls,type) or
            any(not callable(getattr(cls,method,None))
                for method in ("pending","prepare","apply","cleanup","inspect"))):
        raise GrantDenied("TRUSTED_JOURNAL_INTERFACE_INVALID")
    # Live files cannot be modified by untrusted actors if the protected
    # installation ACL is correctly enforced. Still re-hash after import.
    if source_sha(service_root)!=expected_sha256:
        raise GrantDenied("TRUSTED_JOURNAL_CHANGED_DURING_LOAD")
    return cls
