"""Synthetic, privacy-minimizing tests for incident projection. No network/DB/browser."""
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import incident_triage as t


def source():
    return {
        'observation': 'available', 'scope': 'queue_metadata_only',
        'counts': {k: 0 for k in ('queued', 'claimed', 'intent', 'saved', 'done', 'blocked', 'uncertain')},
        'alerts': [], 'attention_required': False,
        'snapshot': {'version': 1, 'observed_at': 1000, 'requests': []},
        'latest_request': None, 'quality_window': {'telegram_delivery_verified': False},
    }


class IncidentTriageTests(unittest.TestCase):
    def test_empty_is_only_no_queue_alerts(self):
        out = t.triage_report(source())
        self.assertEqual(out['status'], 'no_queue_alerts_observed')
        self.assertFalse(out['verified_task_completion'])
        self.assertFalse(out['telegram_delivery_verified'])
        self.assertEqual(out['confidence'], 'queue_metadata_only')
        self.assertFalse(out['safe_auto_recovery'])

    def test_unavailable_not_stopped(self):
        o = t.triage_report({'observation': 'unavailable', 'error': 'database_unavailable'})
        self.assertEqual(o['status'], 'observation_unavailable')
        self.assertEqual(o['confidence'], 'none')
        self.assertEqual(o['issues'][0]['code'], 'NO_TRUSTED_QUEUE_SNAPSHOT')

    def test_unknown_error_text_never_echoes(self):
        out = t.triage_report({'observation': 'unavailable', 'error': 'api_key=private'})
        self.assertNotIn('private', json.dumps(out))
        self.assertEqual(out['observation_error'], 'unclassified_observation_error')

    def test_secret_fields_ignored_no_exfiltration(self):
        src = source()
        src['payload'] = 'PASSWORD_PRIVATE'
        src['latest_request'] = {'key': 'a' * 64, 'reason': 'PRIVATE'}
        src['alerts'] = [{'code': 'new_failures_since_snapshot',
                          'counts': {'blocked:login_required': 1}, 'secret': 'PASSWORD_PRIVATE'}]
        self.assertNotIn('PASSWORD_PRIVATE', json.dumps(t.triage_report(src)))

    def test_uncertain_history_critical_without_new_alert(self):
        src = source(); src['counts']['uncertain'] = 2
        o = t.triage_report(src)
        self.assertEqual(o['issues'][0]['code'], 'UNCERTAIN_HISTORY_PRESENT')
        self.assertEqual(o['issues'][0]['safe_next_step'], 'inspect_all_uncertain_effects_without_replay')

    def test_uncertain_new_failure_never_retried(self):
        src = source(); src['counts']['uncertain'] = 1
        src['alerts'] = [{'code': 'new_failures_since_snapshot', 'counts': {'uncertain:uncertain_send': 1}}]
        o = t.triage_report(src)
        self.assertEqual(o['issues'][0]['code'], 'EXTERNAL_EFFECT_UNCERTAIN')
        self.assertFalse(o['safe_auto_recovery'])

    def test_blocked_post_send_is_not_auto_retryable(self):
        src = source()
        src['alerts'] = [{'code': 'new_failures_since_snapshot', 'counts': {'blocked:send_unconfirmed_timeout': 1}}]
        o = t.triage_report(src)
        self.assertEqual(o['issues'][0]['code'], 'POST_SEND_EVIDENCE_MISSING')
        self.assertIn('no_replay', o['issues'][0]['safe_next_step'])

    def test_known_auth_failure(self):
        src = source(); src['alerts'] = [{'code': 'new_failures_since_snapshot', 'counts': {'blocked:login_required': 1}}]
        self.assertEqual(t.triage_report(src)['issues'][0]['kind'], 'AUTHENTICATION')

    def test_rate_limit_failure(self):
        src = source(); src['alerts'] = [{'code': 'new_failures_since_snapshot', 'counts': {'blocked:rate_limit': 2}}]
        self.assertEqual(t.triage_report(src)['issues'][0]['safe_next_step'], 'verify_provider_retry_after_and_budgets')

    def test_manual_input_is_ambiguous_not_hard_crash(self):
        src = source(); src['alerts'] = [{'code': 'new_failures_since_snapshot', 'counts': {'blocked:manual_input': 1}}]
        self.assertEqual(t.triage_report(src)['issues'][0]['kind'], 'MANUAL_INTERVENTION')

    def test_stale_and_slow_age_not_phase_duration(self):
        src = source(); src['alerts'] = [
            {'code': 'stale_active_requests', 'states': {'saved': {'count': 1, 'oldest_request_age_seconds': 890}}},
            {'code': 'response_age_target_exceeded', 'states': {'queued': {'count': 2, 'oldest_request_age_seconds': 155}}},
        ]
        result = t.triage_report(src)
        self.assertEqual(result['issues'][0]['kind'], 'AGE_NOT_PROOF_OF_WORKER_DEATH')
        self.assertEqual(result['issues'][1]['kind'], 'REQUEST_CREATION_AGE_ONLY')

    def test_blocked_history_without_baseline(self):
        src = source(); src['counts']['blocked'] = 3
        src['alerts'] = [{'code': 'failure_history_without_baseline', 'count': 3}]
        self.assertEqual(t.triage_report(src)['issues'][0]['code'], 'UNBASELINED_FAILURE_HISTORY')

    def test_no_alert_with_old_blocked_history(self):
        src = source(); src['counts']['blocked'] = 1
        self.assertEqual(t.triage_report(src)['issues'][0]['code'], 'BLOCKED_HISTORY_PRESENT')

    def test_sorted_severity_and_bounded_output(self):
        src = source(); src['counts']['uncertain'] = 1
        src['alerts'] = [{'code': 'response_age_target_exceeded', 'states': {'queued': {'count': 1, 'oldest_request_age_seconds': 120}}}]
        issues = t.triage_report(src)['issues']
        self.assertEqual([i['severity'] for i in issues], ['P1', 'P3'])

    def test_invalid_payload(self):
        for source_input in (None, [], '', {}, {'observation': 'complete'},
                             {'observation': 'available', 'scope': 'general'}):
            with self.subTest(input=source_input), self.assertRaises(t.TriageError):
                t.triage_report(source_input)

    def test_missing_counts_rejected(self):
        src = source(); del src['counts']['done']
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_negative_count_rejected(self):
        src = source(); src['counts']['done'] = -1
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_bool_count_rejected(self):
        src = source(); src['counts']['done'] = True
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_unrecognized_alert_fails_closed(self):
        src = source(); src['alerts'] = [{'code': 'AUTO_RETRY_ALL'}]
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_unrecognized_failure_reason_fails_closed(self):
        src = source(); src['alerts'] = [{'code': 'new_failures_since_snapshot',
                                         'counts': {'blocked:secret_api_token': 1}}]
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_raw_url_not_in_output(self):
        src = source(); src['alerts'] = [{'code': 'new_failures_since_snapshot',
                                         'counts': {'blocked:other': 1}}]
        src['secret_url'] = 'ws://127.0.0.1:9876/token'
        self.assertNotIn('ws://', json.dumps(t.triage_report(src)))

    def test_source_report_not_mutated(self):
        src = source(); src2 = deepcopy(src)
        t.triage_report(src)
        self.assertEqual(src, src2)

    def test_invalid_age_kind_fails_closed(self):
        src = source(); src['alerts'] = [{'code': 'stale_active_requests', 'states': {'done': {'count': 1, 'oldest_request_age_seconds': 100}}}]
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_nan_age_rejected(self):
        src = source(); src['alerts'] = [{'code': 'stale_active_requests', 'states': {'queued': {'count': 1, 'oldest_request_age_seconds': float('nan')}}}]
        with self.assertRaises(t.TriageError): t.triage_report(src)

    def test_cli_json_output_and_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'report.json'; path.write_text(json.dumps(source()))
            from io import StringIO
            from unittest.mock import patch
            with patch('sys.stdout', new_callable=StringIO) as stdout:
                self.assertEqual(t.main(['--report', str(path)]), 0)
                self.assertFalse(json.loads(stdout.getvalue())['telegram_delivery_verified'])

    def test_cli_invalid_json_returns_unavailable_no_details(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'report.json'; path.write_text('{"password":')
            from io import StringIO
            from unittest.mock import patch
            with patch('sys.stdout', new_callable=StringIO) as stdout:
                self.assertEqual(t.main(['--report', str(path)]), 2)
                self.assertNotIn('password', stdout.getvalue())

if __name__ == '__main__':
    unittest.main()
