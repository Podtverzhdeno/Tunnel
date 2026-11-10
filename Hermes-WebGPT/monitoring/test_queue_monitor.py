import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import queue_monitor as monitor


class QueueMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'queue # fixture.sqlite'
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute('''CREATE TABLE requests (
                id TEXT PRIMARY KEY, state TEXT, reason TEXT, created REAL,
                payload TEXT, result TEXT, lease TEXT, saved_url TEXT)''')

    def add(self, ident='request-private', state='queued', reason=None, created=900):
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)',
                       (ident, state, reason, created, 'PRIVATE_PAYLOAD',
                        'PRIVATE_REPLY', 'PRIVATE_LEASE', 'https://private.example/chat'))

    def report(self, **kwargs):
        return monitor.observe(self.db, now=1000, **kwargs)

    def test_empty_queue_does_not_claim_end_to_end_health(self):
        r = self.report()
        self.assertEqual(r['scope'], 'queue_metadata_only')
        self.assertFalse(r['attention_required'])
        self.assertNotIn('healthy', r)

    def test_missing_database_is_not_created(self):
        missing = Path(self.tmp.name) / 'missing.sqlite'
        with self.assertRaisesRegex(monitor.ObservationError, 'database_unavailable'):
            monitor.observe(missing)
        self.assertFalse(missing.exists())

    def test_missing_table_is_explicit_schema_failure(self):
        empty = Path(self.tmp.name) / 'empty.sqlite'
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(monitor.ObservationError, 'unsupported_queue_schema'):
            monitor.observe(empty)

    def test_snapshot_does_not_include_message_credentials_url_or_raw_id(self):
        self.add(state='blocked', reason='PRIVATE_REASON')
        serialized = json.dumps(self.report())
        for secret in ('PRIVATE_', 'private.example', 'request-private'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(self.report()['latest_request']['reason'], 'other')

    def test_no_database_changes(self):
        self.add()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.report()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_only_metadata_columns_selected(self):
        statements = []
        connect = sqlite3.connect

        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch.object(monitor.sqlite3, 'connect', side_effect=traced):
            self.report()
        selected = [s for s in statements if s.startswith('SELECT')]
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].startswith('SELECT id, state, reason, created FROM requests'))
        self.assertIn('PRAGMA query_only=ON', statements)

    def test_committed_wal_rows_are_visible(self):
        with contextlib.closing(sqlite3.connect(self.db)) as writer, writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO requests(id,state,created) VALUES ('wal-row','queued',900)")
            writer.commit()
            self.assertEqual(self.report()['counts']['queued'], 1)

    def test_unknown_state_does_not_report_success(self):
        self.add(state='future-new-state')
        with self.assertRaisesRegex(monitor.ObservationError, 'unsupported_queue_record'):
            self.report()

    def test_history_without_baseline_is_not_called_new(self):
        self.add(state='blocked', reason='manual_input')
        self.assertEqual(self.report()['alerts'][0]['code'], 'failure_history_without_baseline')

    def test_identical_terminal_failure_does_not_alert_again(self):
        self.add(state='blocked', reason='manual_input')
        old = self.report()
        self.assertEqual(self.report(baseline=old)['alerts'], [])

    def test_new_manual_input_on_existing_request_alerts(self):
        self.add(state='queued')
        old = self.report()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE requests SET state='blocked',reason='web_manual_input'")
        alert = self.report(baseline=old)['alerts'][0]
        self.assertEqual(alert['counts'], {'blocked:manual_input': 1})

    def test_failure_replacement_alerts_with_unchanged_total_failure_count(self):
        self.add(ident='old', state='blocked', reason='manual_input')
        old = self.report()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE requests SET state='done', reason=NULL WHERE id='old'")
        self.add(ident='new', state='blocked', reason='manual_input')
        r = self.report(baseline=old)
        self.assertEqual(r['counts']['blocked'], old['counts']['blocked'])
        self.assertEqual(r['alerts'][0]['counts'], {'blocked:manual_input': 1})

    def test_new_uncertain_request_alerts(self):
        old = self.report()
        self.add(state='uncertain', reason='web_uncertain_send')
        alert = self.report(baseline=old)['alerts'][0]
        self.assertEqual(alert['counts'], {'uncertain:uncertain_send': 1})

    def test_existing_failure_reason_change_alerts(self):
        self.add(state='blocked', reason='manual_input')
        old = self.report()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE requests SET reason='request_timeout'")
        self.assertEqual(self.report(baseline=old)['alerts'][0]['counts'],
                         {'blocked:request_timeout': 1})

    def test_stale_saved_state_is_included_without_claiming_phase_latency(self):
        self.add(state='saved', created=400)
        r = self.report()
        group = r['alerts'][0]['states']['saved']
        self.assertEqual(group['oldest_request_age_seconds'], 600)
        self.assertFalse(r['timing']['phase_latency_available'])
        self.assertEqual(r['timing']['age_basis'], 'request_created_not_phase_start')

    def test_all_active_states_in_stall_check(self):
        for state in monitor.ACTIVE:
            self.add(ident=state, state=state, created=0)
        self.assertEqual(set(self.report()['alerts'][0]['states']), monitor.ACTIVE)

    def test_recent_active_request_does_not_trigger_stall(self):
        self.add(created=999)
        self.assertEqual(self.report()['alerts'], [])

    def test_row_limit_does_not_silently_hide_active_requests(self):
        self.add(ident='one')
        self.add(ident='two')
        with self.assertRaisesRegex(monitor.ObservationError, 'row_limit_exceeded'):
            self.report(max_rows=1)

    def test_baseline_from_future_rejected(self):
        old = self.report()
        old['snapshot']['observed_at'] = 1001
        with self.assertRaisesRegex(monitor.ObservationError, 'baseline_from_future'):
            self.report(baseline=old)

    def test_database_replacement_requires_rebaseline(self):
        self.add()
        old = self.report()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM requests')
        with self.assertRaisesRegex(monitor.ObservationError, 'baseline_queue_mismatch'):
            self.report(baseline=old)

    def test_reused_id_with_different_creation_time_rejected(self):
        self.add()
        old = self.report()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE requests SET created=950')
        with self.assertRaisesRegex(monitor.ObservationError, 'baseline_queue_mismatch'):
            self.report(baseline=old)

    def test_malformed_baselines_are_rejected_without_crash(self):
        self.add()
        for value in ([], {}, {'version': 1, 'observed_at': 1000, 'requests': [None]},
                      {'version': 1, 'observed_at': 1000, 'requests': [{'key': [], 'state': []}]}):
            with self.subTest(value=value), self.assertRaises(monitor.ObservationError):
                self.report(baseline=value)

    def test_invalid_timestamps_and_thresholds_rejected(self):
        for value in (float('nan'), float('inf'), -1, 0):
            with self.subTest(value=value), self.assertRaises(monitor.ObservationError):
                self.report(stale_seconds=value)

    def test_queue_clock_in_future_rejected(self):
        self.add(created=1001)
        with self.assertRaisesRegex(monitor.ObservationError, 'queue_clock_in_future'):
            self.report()

    def test_cli_unavailable_returns_two_without_private_path(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = monitor.main(['--db', str(Path(self.tmp.name) / 'private-missing.sqlite')])
        self.assertEqual(rc, 2)
        self.assertNotIn(self.tmp.name, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['observation'], 'unavailable')

    def test_cli_alert_exit_one_and_baseline_success_exit_zero(self):
        self.add(state='blocked', reason='manual_input', created=0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(monitor.main(['--db', str(self.db)]), 1)
        baseline = Path(self.tmp.name) / 'baseline.json'
        baseline.write_text(output.getvalue(), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.main(['--db', str(self.db), '--baseline', str(baseline)]), 0)


if __name__ == '__main__':
    unittest.main()
