"""No Relay/browser/Telegram calls: synthetic immutable monitor reports only."""
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import incident_sampler as s


def valid_result(baseline=None):
    current = {'version': 1, 'observed_at': 1000, 'requests': [], 'reason_catalog_version': 3}
    return {
        'observation': 'available', 'scope': 'queue_metadata_only',
        'counts': {name: 0 for name in ('queued','claimed','intent','saved','done','blocked','uncertain')},
        'alerts': [], 'snapshot': current,
    }


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'real-relay.db'
        self.source.write_bytes(b'FAKE_RELAY_NOT_READ_BY_UNIT_TEST')
        self.journal = Path(self.tmp.name) / 'private-state' / 'incident.db'

    def runmock(self, result=None, **kwargs):
        if result is None:
            result = valid_result()
        with patch.object(s.queue_monitor, 'observe', return_value=result, create=True) as observe:
            state = s.run_cycle(self.source, self.journal, now=1000, **kwargs)
            return state, observe

    def test_atomic_cycle_stores_baseline_and_event(self):
        report, _ = self.runmock()
        self.assertEqual(report['status'], 'no_queue_alerts_observed')
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monitor_baseline').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 1)
        self.assertFalse(b'FAKE_RELAY' in self.journal.read_bytes())

    def test_passes_last_snapshot_to_next_run(self):
        self.runmock()
        _, mock = self.runmock()
        self.assertEqual(mock.call_args.kwargs['baseline']['version'], 1)

    def test_observer_outage_preserves_previous_baseline(self):
        self.runmock()
        with patch.object(s.queue_monitor, 'observe', side_effect=s.queue_monitor.ObservationError('database_unavailable'), create=True):
            report = s.run_cycle(self.source, self.journal)
        self.assertEqual(report['status'], 'observation_unavailable')
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monitor_baseline').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 2)

    def test_rollback_when_unencodable_baseline(self):
        self.runmock()
        bad = valid_result(); bad['snapshot'] = {'untrusted': object()}
        with patch.object(s.queue_monitor, 'observe', return_value=bad, create=True):
            with self.assertRaises(TypeError):
                s.run_cycle(self.source, self.journal)
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute('SELECT snapshot FROM monitor_baseline').fetchone()[0])['version'], 1)

    def test_reject_journal_over_source(self):
        with self.assertRaisesRegex(s.SamplerError, 'invalid_journal_path'):
            s.run_cycle(self.source, self.source)

    def test_reject_symlink_journal(self):
        fake = self.journal.parent / 'link.db'
        fake.parent.mkdir(exist_ok=True)
        try:
            fake.symlink_to(self.source)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(s.SamplerError, 'invalid_journal_path'):
            s.run_cycle(self.source, fake)

    def test_retention_is_bounded(self):
        for _ in range(7):
            self.runmock(keep_events=3)
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 3)

    def test_readonly_source_content_never_logged(self):
        self.runmock()
        raw = self.journal.read_bytes()
        self.assertNotIn(b'FAKE_RELAY', raw)
        self.assertNotIn(b'real-relay.db', raw)

    def test_no_automatic_mutation_or_send(self):
        with patch.object(s.queue_monitor, 'observe', return_value=valid_result(), create=True) as mock:
            s.run_cycle(self.source, self.journal)
            self.assertEqual(mock.call_count, 1)

    def test_uncertain_state_remains_recorded(self):
        report = valid_result()
        report['counts']['uncertain'] = 1
        out, _ = self.runmock(result=report)
        self.assertEqual(out['issues'][0]['code'], 'UNCERTAIN_HISTORY_PRESENT')
        self.assertFalse(out['safe_auto_recovery'])

    def test_bad_retention_rejected(self):
        with self.assertRaisesRegex(s.SamplerError, 'invalid_retention'):
            self.runmock(keep_events=-1)

    def test_unavailable_no_secrets_in_record(self):
        self.runmock()
        with patch.object(s.queue_monitor, 'observe', side_effect=RuntimeError('secret=password'), create=True):
            status = s.run_cycle(self.source, self.journal)
        self.assertEqual(status['status'], 'observation_unavailable')
        self.assertNotIn(b'password', self.journal.read_bytes())


    def test_samplers_serialize_without_losing_baseline_or_log(self):
        from concurrent.futures import ThreadPoolExecutor
        with patch.object(s.queue_monitor, 'observe', return_value=valid_result(), create=True):
            with ThreadPoolExecutor(max_workers=12) as pool:
                statuses = list(pool.map(lambda _: s.run_cycle(self.source, self.journal, now=1000), range(24)))
        self.assertEqual(len(statuses), 24)
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 24)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monitor_baseline').fetchone()[0], 1)

    def test_corrupt_baseline_is_not_silently_reset(self):
        self.runmock()
        with contextlib.closing(sqlite3.connect(self.journal)) as db, db:
            db.execute("UPDATE monitor_baseline SET snapshot='not-json' WHERE id=1")
        with patch.object(s.queue_monitor, 'observe', return_value=valid_result(), create=True):
            with self.assertRaises(ValueError):
                s.run_cycle(self.source, self.journal)
        with contextlib.closing(sqlite3.connect(self.journal)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_events').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT snapshot FROM monitor_baseline').fetchone()[0], 'not-json')

    def test_cli_unavailable_has_no_user_path_or_credentials(self):
        from io import StringIO
        with patch.object(s.queue_monitor, 'observe', side_effect=RuntimeError('secret_token_123'), create=True):
            with patch('sys.stdout', new_callable=StringIO) as stream:
                rc = s.main(['--db', str(self.source), '--journal', str(self.journal)])
                out = json.loads(stream.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(out['status'], 'observation_unavailable')
        self.assertNotIn('secret_token_123', json.dumps(out))
        self.assertNotIn(str(self.source), json.dumps(out))

if __name__ == '__main__':
    unittest.main()
