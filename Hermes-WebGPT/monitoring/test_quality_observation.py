import contextlib
import io
import json
import unittest
import test_queue_monitor as fixtures
import queue_monitor as monitor


class QualityObservationTests(unittest.TestCase):
    setUp = fixtures.QueueMonitorTests.setUp
    add = fixtures.QueueMonitorTests.add
    report = fixtures.QueueMonitorTests.report

    def test_known_browser_failure_is_reported_precisely(self):
        old=self.report()
        self.add(state='blocked',reason='web_content_tab_not_ready')
        row=self.report(baseline=old)
        self.assertEqual(row['alerts'][0]['counts'],{'blocked:content_tab_not_ready':1})

    def test_legacy_reason_refinement_is_not_a_new_failure(self):
        self.add(state='blocked',reason='tab_create_unverified')
        old=self.report()
        old['snapshot'].pop('reason_catalog_version')
        old['snapshot']['requests'][0]['reason']='other'
        row=self.report(baseline=old)
        self.assertEqual(row['alerts'],[])
        self.assertEqual(row['reason_catalog_refinements'],{'blocked:tab_create_unverified':1})

    def test_new_failure_remains_new_after_catalog_upgrade(self):
        old=self.report();old['snapshot'].pop('reason_catalog_version')
        self.add(state='blocked',reason='tab_create_unverified')
        self.assertEqual(self.report(baseline=old)['alerts'][0]['counts'],
                         {'blocked:tab_create_unverified':1})

    def test_latency_target_does_not_claim_stale_state_or_phase_time(self):
        self.add(created=799)
        row=self.report(latency_target_seconds=120)
        self.assertEqual([a['code'] for a in row['alerts']],['response_age_target_exceeded'])
        self.assertEqual(row['alerts'][0]['states']['queued']['oldest_request_age_seconds'],201)
        self.assertFalse(row['timing']['phase_latency_available'])

    def test_completed_request_is_not_an_active_latency_warning(self):
        self.add(state='done',created=0)
        row=self.report(latency_target_seconds=120)
        self.assertEqual(row['alerts'],[])

    def test_target_is_optional_and_independent_of_stale_threshold(self):
        self.add(created=700)
        self.assertEqual(self.report()['alerts'],[])
        self.assertEqual(self.report(latency_target_seconds=120)['alerts'][0]['code'],
                         'response_age_target_exceeded')

    def test_window_uses_creation_time_and_counts_terminal_outcomes(self):
        self.add('done',state='done',created=950)
        self.add('bad',state='blocked',reason='manual_input',created=960)
        self.add('unknown',state='uncertain',reason='uncertain_send',created=970)
        self.add('active',state='claimed',created=980)
        self.add('historical',state='done',created=899)
        window=self.report(window_seconds=100)['quality_window']
        self.assertEqual(window['terminal_observations'],3)
        self.assertEqual(window['inference_completion_ratio'],1/3)
        self.assertEqual(window['counts']['done'],1)
        self.assertEqual(window['basis'],'request_created_not_completed')
        self.assertFalse(window['telegram_delivery_verified'])
        self.assertFalse(window['answer_correctness_verified'])

    def test_empty_or_active_only_window_has_unknown_ratio(self):
        self.assertIsNone(self.report()['quality_window']['inference_completion_ratio'])
        self.add()
        self.assertIsNone(self.report()['quality_window']['inference_completion_ratio'])

    def test_window_boundary_is_inclusive(self):
        self.add(state='done',created=900)
        self.assertEqual(self.report(window_seconds=100)['quality_window']['counts']['done'],1)

    def test_invalid_targets_and_windows_fail_closed(self):
        for value in (0,-1,True,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(monitor.ObservationError):
                self.report(latency_target_seconds=value)
        for value in (0,86401,True):
            with self.subTest(value=value),self.assertRaises(monitor.ObservationError):
                self.report(window_seconds=value)

    def test_invalid_catalog_versions_fail_closed(self):
        for value in (True,[],monitor.REASON_CATALOG_VERSION+1,'2'):
            old=self.report();old['snapshot']['reason_catalog_version']=value
            with self.subTest(value=value),self.assertRaises(monitor.ObservationError):
                self.report(baseline=old)

    def test_cli_exposes_target_without_private_payload_or_delivery_claim(self):
        self.add(state='saved',created=1)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=monitor.main(['--db',str(self.db),'--latency-target-seconds','120','--window-seconds','3600'])
        self.assertEqual(code,1)
        row=json.loads(output.getvalue())
        self.assertFalse(row['quality_window']['telegram_delivery_verified'])
        self.assertNotIn('PRIVATE_',output.getvalue())


if __name__=='__main__':unittest.main()
