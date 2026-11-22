"""Legacy queue_monitor JSON baseline compatibility, read-only to Relay.

Only called explicitly. Failed or uncertain requests are never auto-acknowledged.
For ongoing observability use the atomic SQLite incident_sampler instead;
do not run both drivers against the same baseline path.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from . import queue_monitor
except ImportError:
    import queue_monitor

MAX_BASELINE_BYTES = 4 * 1024 * 1024


def _load_baseline(path):
    if path.is_symlink():
        raise queue_monitor.ObservationError('invalid_baseline')
    if not path.exists():
        return None, None
    if not path.is_file() or path.stat().st_size > MAX_BASELINE_BYTES:
        raise queue_monitor.ObservationError('baseline_too_large')
    data = path.read_bytes()
    try:
        content = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise queue_monitor.ObservationError('invalid_baseline') from exc
    return content, hashlib.sha256(data).hexdigest()


def _save_clean_snapshot(path, old_digest, snapshot):
    if path.is_symlink():
        raise queue_monitor.ObservationError('invalid_baseline')
    # Optimistic check catches common concurrent baseline replacement; it is
    # NOT a cross-process CAS. Production concurrency uses incident_sampler's
    # BEGIN IMMEDIATE transaction, not this deprecated file-baseline path.
    if old_digest is not None:
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise queue_monitor.ObservationError('baseline_unavailable_or_invalid') from exc
        if current != old_digest:
            raise queue_monitor.ObservationError('baseline_queue_mismatch')
    elif path.exists():
        raise queue_monitor.ObservationError('baseline_queue_mismatch')
    data = json.dumps({'snapshot': snapshot}, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_BASELINE_BYTES:
        raise queue_monitor.ObservationError('baseline_too_large')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         prefix='.baseline-', suffix='.tmp',
                                         delete=False) as f:
            temp_path = Path(f.name)
            os.chmod(temp_path, 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def execute(db_path, baseline_path, *, now=None, advance_if_clean=False):
    """Return sanitized report + a diagnostic exit code; never modify Relay DB.

    Passing advance_if_clean explicitly allows the legacy JSON baseline to
    advance only when no alerts and no retained uncertain-effect rows exist.
    This is NOT a user/Telegram ACK, completed model turn or retry approval.
    """
    source = Path(db_path).expanduser().resolve()
    target = Path(baseline_path).expanduser()
    if target.resolve() == source or target.is_symlink():
        raise queue_monitor.ObservationError('invalid_baseline')
    prior, digest = _load_baseline(target)
    report = queue_monitor.observe(source, baseline=prior, now=now)
    alerts = report['alerts']
    new_counts = {}
    for item in alerts:
        if item['code'] == 'new_failures_since_snapshot':
            new_counts = dict(item['counts'])
    uncertain_count = int(report['counts']['uncertain'])
    unsafe = bool(alerts) or bool(uncertain_count)
    advanced = False
    if advance_if_clean and not unsafe:
        _save_clean_snapshot(target, digest, report['snapshot'])
        advanced = True
    return {
        'observation': 'available', 'scope': 'queue_metadata_only',
        'counts': report['counts'], 'new_failure_counts': new_counts,
        'baseline_advanced': advanced, 'automatic_retry_authorized': False,
        'telegram_delivery_verified': False,
        'effect_outcome_verified': False,
        'reason': ('RECONCILIATION_REQUIRED' if uncertain_count else
                   'QUEUE_ALERTS_PRESENT' if alerts else 'NO_NEW_QUEUE_ALERTS'),
    }, (1 if unsafe else 0)
