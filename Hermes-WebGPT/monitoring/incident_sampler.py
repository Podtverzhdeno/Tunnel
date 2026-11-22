"""Opt-in, bounded, local-only Hermes queue sampler; never changes Relay.

Every run reads only the existing queue monitor's four metadata columns.
State snapshot and incident record commit in ONE SQLite transaction so a crash
cannot acknowledge a new baseline without its corresponding incident journal.
No model/browser/Telegram actions, Send retry, agent restart, or approval.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

try:
    from . import queue_monitor, incident_triage
except ImportError:
    import queue_monitor
    import incident_triage


class SamplerError(RuntimeError):
    pass


def _encode(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _unknown(code='observer_unavailable'):
    return incident_triage.triage_report({'observation': 'unavailable', 'error': code})


def run_cycle(relay_db, journal_db, *, max_rows=10000, stale_seconds=600,
              latency_target_seconds=None, keep_events=2000, now=None):
    """Return a sanitized classification; persist snapshot and incident atomically.

    Input DB read-only (through queue_monitor); separate journal is writable.
    Many sampler processes serialize using SQLite BEGIN IMMEDIATE.
    """
    source = Path(relay_db).expanduser().resolve()
    target = Path(journal_db).expanduser()
    if target.is_symlink() or target.resolve() == source:
        raise SamplerError('invalid_journal_path')
    if type(keep_events) is not int or not 1 <= keep_events <= 10000:
        raise SamplerError('invalid_retention')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with closing(sqlite3.connect(target, timeout=10, isolation_level=None)) as db:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS monitor_baseline (
                  id INTEGER PRIMARY KEY CHECK(id=1), snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incident_events (
                  event_no INTEGER PRIMARY KEY AUTOINCREMENT,
                  received_at REAL NOT NULL,
                  report TEXT NOT NULL
                );
            ''')
            try:
                db.execute('BEGIN IMMEDIATE')
                item = db.execute('SELECT snapshot FROM monitor_baseline WHERE id=1').fetchone()
                baseline = None if not item else json.loads(item[0])
                try:
                    result = queue_monitor.observe(
                        source, baseline=baseline, now=now, stale_seconds=stale_seconds,
                        max_rows=max_rows, latency_target_seconds=latency_target_seconds)
                    summary = incident_triage.triage_report(result)
                    next_baseline = result['snapshot']
                except queue_monitor.ObservationError as exc:
                    # Preserve only an allowlisted symbolic diagnostic code.
                    summary = _unknown(str(exc))
                    next_baseline = None
                except Exception:
                    # Unexpected error text may contain local secrets: discard it.
                    summary = _unknown()
                    next_baseline = None
                if next_baseline is not None:
                    snapshot_text = _encode(next_baseline)
                    if len(snapshot_text) > 4 * 1024 * 1024:
                        raise SamplerError('snapshot_too_large')
                    db.execute('INSERT INTO monitor_baseline(id,snapshot) VALUES(1,?) '
                               'ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot',
                               (snapshot_text,))
                record = _encode(summary)
                if len(record) > 1024 * 1024:
                    raise SamplerError('incident_record_too_large')
                tick = time.time() if now is None else float(now)
                if not 0 <= tick <= 1e15:
                    raise SamplerError('invalid_clock')
                db.execute('INSERT INTO incident_events(received_at,report) VALUES(?,?)',
                           (tick, record))
                db.execute('DELETE FROM incident_events WHERE event_no <= '
                           'COALESCE((SELECT MAX(event_no) - ? FROM incident_events), -1)',
                           (keep_events,))
                db.execute('COMMIT')
                return summary
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
    except sqlite3.Error as exc:
        raise SamplerError('journal_unavailable') from exc


def main(argv=None):
    p = argparse.ArgumentParser(description='Read-only Relay queue sampler with local atomic incident journal')
    p.add_argument('--db', required=True, help='Exact existing local Relay SQLite path')
    p.add_argument('--journal', required=True, help='Different local private SQLite path')
    p.add_argument('--stale-seconds', type=float, default=600)
    p.add_argument('--latency-target-seconds', type=float)
    p.add_argument('--max-rows', type=int, default=10000)
    p.add_argument('--keep-events', type=int, default=2000)
    p.add_argument('--interval', type=float, default=0,
                   help='Seconds; 0=one cycle. Positive value watches in foreground only.')
    args = p.parse_args(argv)
    if not 0 <= args.interval <= 86400 or 0 < args.interval < 5:
        p.error('invalid_interval')
    try:
        while True:
            try:
                report = run_cycle(args.db, args.journal, max_rows=args.max_rows,
                                   stale_seconds=args.stale_seconds,
                                   latency_target_seconds=args.latency_target_seconds,
                                   keep_events=args.keep_events)
            except (SamplerError, OSError, ValueError):
                report = _unknown()
            print(_encode(report), flush=True)
            if args.interval == 0:
                return 1 if report.get('issues') and report['status'] != 'observation_unavailable' else (2 if report['status'] == 'observation_unavailable' else 0)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
