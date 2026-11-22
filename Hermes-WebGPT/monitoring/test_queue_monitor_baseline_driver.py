"""Legacy JSON baseline compatibility tests, isolated; no real Relay write."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import queue_monitor_baseline as driver


def report(alerts=None, uncertain=0):
    return {
        'observation': 'available', 'scope': 'queue_metadata_only',
        'alerts': alerts or [],
        'counts': {s:(uncertain if s=='uncertain' else 0) for s in
                   ('queued','claimed','intent','saved','done','blocked','uncertain')},
        'snapshot': {'version':1,'reason_catalog_version':3,'observed_at':1000,'requests':[]},
    }


class LegacyBaselineCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        root = Path(self.t.name)
        self.source = root/'relay.sqlite'
        self.source.write_bytes(b'RELAY_INPUT_UNCHANGED')
        self.baseline = root/'baseline.json'
        self.baseline.write_text(json.dumps({'snapshot':report()['snapshot']}))

    def test_precise_new_failure_never_acknowledged(self):
        before = self.baseline.read_bytes(); src=self.source.read_bytes()
        event = {'code':'new_failures_since_snapshot','counts':{'uncertain:user_message_not_confirmed':1}}
        with patch.object(driver.queue_monitor,'observe',return_value=report([event],uncertain=1),create=True):
            out,code=driver.execute(self.source,self.baseline,advance_if_clean=True)
        self.assertEqual(code,1)
        self.assertFalse(out['baseline_advanced'])
        self.assertFalse(out['automatic_retry_authorized'])
        self.assertEqual(out['new_failure_counts'],event['counts'])
        self.assertEqual(self.baseline.read_bytes(),before)
        self.assertEqual(self.source.read_bytes(),src)

    def test_uncertain_history_without_new_alert_remains_blocked(self):
        with patch.object(driver.queue_monitor,'observe',return_value=report(uncertain=2),create=True):
            out,code=driver.execute(self.source,self.baseline,advance_if_clean=True)
        self.assertEqual(code,1)
        self.assertEqual(out['reason'],'RECONCILIATION_REQUIRED')
        self.assertFalse(out['baseline_advanced'])

    def test_clean_cas_writes_only_baseline(self):
        with patch.object(driver.queue_monitor,'observe',return_value=report(),create=True):
            out,code=driver.execute(self.source,self.baseline,advance_if_clean=True)
        self.assertEqual(code,0)
        self.assertTrue(out['baseline_advanced'])
        self.assertEqual(self.source.read_bytes(),b'RELAY_INPUT_UNCHANGED')
        self.assertEqual(json.loads(self.baseline.read_text())['snapshot']['version'],1)

    def test_clean_not_advanced_without_explicit_flag(self):
        before=self.baseline.read_bytes()
        with patch.object(driver.queue_monitor,'observe',return_value=report(),create=True):
            out,code=driver.execute(self.source,self.baseline)
        self.assertEqual(code,0)
        self.assertFalse(out['baseline_advanced'])
        self.assertEqual(self.baseline.read_bytes(),before)

    def test_bad_json_fails_closed(self):
        self.baseline.write_text('{bad')
        with self.assertRaises(driver.queue_monitor.ObservationError):
            driver.execute(self.source,self.baseline)

    def test_source_baseline_alias_is_rejected(self):
        with self.assertRaises(driver.queue_monitor.ObservationError):
            driver.execute(self.source,self.source)

    def test_manual_previous_baseline_change_is_detected(self):
        before = driver._load_baseline(self.baseline)[1]
        self.baseline.write_text('{}')
        with self.assertRaises(driver.queue_monitor.ObservationError):
            driver._save_clean_snapshot(self.baseline,before,report()['snapshot'])

if __name__=='__main__':
    unittest.main()
