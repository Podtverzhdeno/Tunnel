"""Confirmed reason codes, conservative catalog migration and no auto-ack."""
import contextlib
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

import queue_monitor as monitor
import queue_monitor_baseline as driver
import test_queue_monitor as fixtures


class ConfirmationObservationTests(unittest.TestCase):
    setUp = fixtures.QueueMonitorTests.setUp
    add = fixtures.QueueMonitorTests.add
    report = fixtures.QueueMonitorTests.report

    def baseline_at(self, version, reason='other'):
        baseline = self.report()
        baseline['snapshot']['reason_catalog_version'] = version
        for row in baseline['snapshot']['requests']:
            row['reason'] = reason
        return baseline

    def test_three_actual_failure_shapes_have_precise_reason(self):
        old = self.report()
        for index in range(3):
            self.add(str(index), state='uncertain', reason='web_user_message_not_confirmed')
        report = self.report(baseline=old)
        self.assertEqual(report['alerts'][0]['counts'],
                         {'uncertain:user_message_not_confirmed': 3})
        self.assertEqual(report['counts']['uncertain'], 3)

    def test_v2_existing_other_is_a_refinement_not_a_new_failure(self):
        self.add(state='uncertain', reason='user_message_not_confirmed')
        report = self.report(baseline=self.baseline_at(2))
        self.assertEqual(report['alerts'], [])
        self.assertEqual(report['reason_catalog_refinements'],
                         {'uncertain:user_message_not_confirmed': 1})
        self.assertEqual(report['quality_window']['counts']['uncertain'], 1)

    def test_v1_existing_other_can_be_refined(self):
        self.add(state='uncertain', reason='assistant_timeout')
        report = self.report(baseline=self.baseline_at(1))
        self.assertEqual(report['alerts'], [])
        self.assertEqual(report['reason_catalog_refinements'], {'uncertain:assistant_timeout': 1})

    def test_v2_other_to_already_known_v2_code_is_not_silenced(self):
        self.add(state='blocked', reason='content_tab_not_ready')
        report = self.report(baseline=self.baseline_at(2))
        self.assertEqual(report['reason_catalog_refinements'], {})
        self.assertEqual(report['alerts'][0]['counts'], {'blocked:content_tab_not_ready': 1})

    def test_same_catalog_other_transition_stays_an_alert(self):
        self.add(state='uncertain', reason='user_message_not_confirmed')
        report = self.report(baseline=self.baseline_at(3))
        self.assertEqual(report['reason_catalog_refinements'], {})
        self.assertEqual(report['alerts'][0]['counts'], {'uncertain:user_message_not_confirmed': 1})

    def test_new_identity_is_not_hidden_by_catalog_upgrade(self):
        old = self.report()
        old['snapshot']['reason_catalog_version'] = 2
        self.add(state='uncertain', reason='user_message_not_confirmed')
        self.assertEqual(self.report(baseline=old)['alerts'][0]['counts'],
                         {'uncertain:user_message_not_confirmed': 1})

    def test_active_to_uncertain_is_not_hidden_by_catalog_upgrade(self):
        self.add()
        old = self.baseline_at(2)
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE requests SET state='uncertain',reason='user_message_not_confirmed'")
        self.assertEqual(self.report(baseline=old)['alerts'][0]['counts'],
                         {'uncertain:user_message_not_confirmed': 1})

    def test_old_catalog_cannot_claim_future_reason_codes(self):
        self.add(state='uncertain', reason='user_message_not_confirmed')
        old = self.baseline_at(2, 'user_message_not_confirmed')
        with self.assertRaisesRegex(monitor.ObservationError, 'invalid_baseline'):
            self.report(baseline=old)

    def test_source_confirmed_timeout_and_handoff_codes_are_allowlisted(self):
        for reason in ('assistant_timeout', 'launch_or_transport_unverified',
                       'content_document_binding_unverified', 'recovery_binding_missing'):
            with self.subTest(reason=reason):
                self.assertEqual(monitor.reason_code(reason), reason)

    def test_native_web_prefix_is_not_stripped_from_a_known_reason(self):
        for reason in monitor.REASONS:
            with self.subTest(reason=reason):
                self.assertEqual(monitor.reason_code(reason), reason)
        self.assertEqual(monitor.reason_code('web_web_waiter_timeout'), 'web_waiter_timeout')

    def test_unknown_private_text_is_still_redacted(self):
        for reason in ('user_message_not_confirmed SECRET',
                       'web_user_message_not_confirmed:https://private',
                       'untrusted_new_reason', None, True):
            with self.subTest(reason=reason):
                self.assertIn(monitor.reason_code(reason), {'none', 'other'})

    def test_driver_reports_precise_failure_without_ack_or_queue_write(self):
        baseline_path = Path(self.tmp.name) / 'baseline.json'
        baseline_path.write_text(json.dumps({'snapshot': self.report()['snapshot']}), encoding='utf8')
        self.add(state='uncertain', reason='user_message_not_confirmed')
        baseline_before = baseline_path.read_bytes()
        queue_before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report, code = driver.execute(self.db, baseline_path, now=1000, advance_if_clean=True)
        self.assertEqual(code, 1)
        self.assertEqual(report['new_failure_counts'], {'uncertain:user_message_not_confirmed': 1})
        self.assertFalse(report['baseline_advanced'])
        self.assertFalse(report['automatic_retry_authorized'])
        self.assertEqual(baseline_path.read_bytes(), baseline_before)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), queue_before)

    def test_snapshot_v3_remains_readable_after_restart(self):
        self.add(state='uncertain', reason='user_message_not_confirmed')
        baseline = json.loads(json.dumps(self.report()))
        report = self.report(baseline=baseline)
        self.assertEqual(report['snapshot']['reason_catalog_version'], 3)
        self.assertEqual(report['alerts'], [])


if __name__ == '__main__':
    unittest.main()
