"""Read-only Relay queue metadata observer. Never imports or starts Relay."""

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time


STATES = frozenset({'queued', 'claimed', 'intent', 'saved', 'done', 'blocked', 'uncertain'})
ACTIVE = frozenset({'queued', 'claimed', 'intent', 'saved'})
FAILURES = frozenset({'blocked', 'uncertain'})
REASONS = frozenset({
    'manual_input', 'uncertain_send', 'content_start_unverified', 'request_timeout',
    'prompt_too_large', 'high_unverified', 'missing_send', 'reply_not_json',
    'client_disconnected', 'claim_delivery_failed', 'relay_shutting_down',
    'worker_failed', 'terminal_state_changed', 'login_required', 'rate_limit',
})


class ObservationError(Exception):
    pass


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ObservationError('invalid_timestamp')
    return float(value)


def reason_code(value):
    if value is None or value == '':
        return 'none'
    if not isinstance(value, str):
        return 'other'
    value = value.removeprefix('web_')
    return value if value in REASONS else 'other'


def read_metadata(db_path, max_rows=10000):
    """Only four columns; payload/result/worker/lease/URL are never selected."""
    if not 1 <= max_rows <= 100000:
        raise ObservationError('invalid_row_limit')
    try:
        path = Path(db_path).resolve(strict=True)
        if not path.is_file():
            raise ObservationError('database_not_file')
    except OSError as exc:
        raise ObservationError('database_unavailable') from exc
    deadline = time.monotonic() + 3
    try:
        # mode=ro fails instead of creating a missing DB. Do not use immutable:
        # the live Relay uses WAL and committed WAL rows must remain visible.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            columns = {r[1] for r in db.execute('PRAGMA table_info(requests)')}
            if not {'id', 'state', 'reason', 'created'} <= columns:
                raise ObservationError('unsupported_queue_schema')
            rows = db.execute(
                'SELECT id, state, reason, created FROM requests LIMIT ?',
                (max_rows + 1,),
            ).fetchall()
            if len(rows) > max_rows:
                raise ObservationError('row_limit_exceeded')
    except sqlite3.Error as exc:
        raise ObservationError('database_read_failed') from exc
    output = []
    keys = set()
    for ident, state, reason, created in rows:
        if not isinstance(ident, str) or not ident or state not in STATES:
            raise ObservationError('unsupported_queue_record')
        key = hashlib.sha256(ident.encode('utf-8')).hexdigest()
        if key in keys:
            raise ObservationError('duplicate_request_identity')
        keys.add(key)
        output.append({'key': key, 'state': state, 'reason': reason_code(reason),
                       'created': timestamp(created)})
    return output


def baseline_records(baseline, now):
    if baseline is None:
        return None, None
    if not isinstance(baseline, dict):
        raise ObservationError('invalid_baseline')
    snapshot = baseline.get('snapshot', baseline)
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
        raise ObservationError('invalid_baseline')
    observed = timestamp(snapshot.get('observed_at'))
    if observed > now:
        raise ObservationError('baseline_from_future')
    rows = snapshot.get('requests')
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ObservationError('invalid_baseline')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ObservationError('invalid_baseline')
        key, state, reason = row.get('key'), row.get('state'), row.get('reason')
        if (not isinstance(key, str) or len(key) != 64 or
                any(c not in '0123456789abcdef' for c in key) or key in result or
                not isinstance(state, str) or state not in STATES or
                not isinstance(reason, str) or reason not in REASONS | {'none', 'other'}):
            raise ObservationError('invalid_baseline')
        created = timestamp(row.get('created'))
        if created > observed:
            raise ObservationError('invalid_baseline')
        result[key] = {'state': state, 'reason': reason, 'created': created}
    return result, observed


def observe(db_path, baseline=None, now=None, stale_seconds=600, max_rows=10000):
    stale_seconds = timestamp(stale_seconds)
    if stale_seconds == 0:
        raise ObservationError('invalid_stale_threshold')
    rows = read_metadata(db_path, max_rows)
    now = timestamp(time.time() if now is None else now)
    previous, previous_at = baseline_records(baseline, now)
    if any(row['created'] > now for row in rows):
        raise ObservationError('queue_clock_in_future')
    if previous is not None:
        current = {row['key']: row for row in rows}
        if any(key not in current or current[key]['created'] != old['created']
               for key, old in previous.items()):
            raise ObservationError('baseline_queue_mismatch')
    counts = {state: 0 for state in sorted(STATES)}
    new_failures, stale = {}, {}
    for row in rows:
        state = row['state']
        counts[state] += 1
        old = previous.get(row['key']) if previous is not None else None
        if (previous is not None and state in FAILURES and
                (old is None or (old['state'], old['reason']) != (state, row['reason']))):
            code = state + ':' + row['reason']
            new_failures[code] = new_failures.get(code, 0) + 1
        age = now - row['created']
        if state in ACTIVE and age >= stale_seconds:
            group = stale.setdefault(state, {'count': 0, 'oldest_request_age_seconds': 0})
            group['count'] += 1
            group['oldest_request_age_seconds'] = max(group['oldest_request_age_seconds'], age)
    alerts = []
    if previous is None and any(counts[s] for s in FAILURES):
        alerts.append({'code': 'failure_history_without_baseline',
                       'count': sum(counts[s] for s in FAILURES)})
    if new_failures:
        alerts.append({'code': 'new_failures_since_snapshot', 'counts': new_failures})
    if stale:
        alerts.append({'code': 'stale_active_requests', 'states': stale})
    latest = max(rows, key=lambda row: (row['created'], row['key']), default=None)
    return {
        'observation': 'available', 'attention_required': bool(alerts),
        'scope': 'queue_metadata_only', 'counts': counts, 'alerts': alerts,
        'baseline_observed_at': previous_at, 'latest_request': latest,
        'timing': {'age_basis': 'request_created_not_phase_start',
                   'phase_latency_available': False},
        'snapshot': {'version': 1, 'observed_at': now,
                     'requests': sorted(rows, key=lambda row: row['key'])},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Existing Relay SQLite file')
    parser.add_argument('--baseline', help='Previous report JSON; read only')
    parser.add_argument('--stale-seconds', type=float, default=600)
    parser.add_argument('--max-rows', type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        baseline = None
        if args.baseline:
            with open(args.baseline, 'rb') as source:
                data = source.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ObservationError('baseline_too_large')
            baseline = json.loads(data)
        report = observe(args.db, baseline=baseline,
                         stale_seconds=args.stale_seconds, max_rows=args.max_rows)
    except (ObservationError, OSError, ValueError, RecursionError) as exc:
        # Never print exception details: they may contain a local path or data.
        code = str(exc) if isinstance(exc, ObservationError) else 'baseline_unavailable_or_invalid'
        print(json.dumps({'observation': 'unavailable', 'attention_required': True,
                          'error': code}))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 1 if report['attention_required'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
