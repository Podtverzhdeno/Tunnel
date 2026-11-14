"""Offline negative and positive tests for already-running DEV smoke."""
import unittest
from types import SimpleNamespace

from quickstart.a4.verify_dev_running import verify_dev_start


class DevRunningSmokeTests(unittest.TestCase):
    def fixture(self):
        return {
            "relay": {"action": "already_running", "port": 18765},
            "gateway": {"action": "already_running", "pid": 9984},
            "receipts": {"bridge_process.json": "sha-before"},
        }

    def test_attested_idempotent_dev_passes_and_uses_flag(self):
        calls=[]
        def execute(argv, **kw):
            calls.append((argv, kw))
            return SimpleNamespace(returncode=0)
        report=verify_dev_start(inspect=lambda: self.fixture(), execute=execute)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["service_identities_unchanged"])
        self.assertEqual(len(calls),1)
        self.assertIn("-DevMode",calls[0][0])
        self.assertNotIn("-ResetKey",calls[0][0])
        self.assertNotIn("-DryRun",calls[0][0])
        self.assertEqual(report["messages_sent_by_test"],0)
        self.assertEqual(report["files_uploaded_by_test"],0)

    def test_does_not_launch_if_any_service_unattested(self):
        called=[]
        report=verify_dev_start(inspect=lambda:None,
                                execute=lambda *a,**k:called.append(a))
        self.assertEqual(report["status"],"SKIP")
        self.assertFalse(report["launch_attempted"])
        self.assertEqual(called,[])

    def test_service_replacement_after_launch_is_failure(self):
        before=self.fixture()
        after=self.fixture()
        after["gateway"]["pid"]=9876
        reports=iter([before,after])
        report=verify_dev_start(inspect=lambda:next(reports),
                                execute=lambda *a,**kw:SimpleNamespace(returncode=0))
        self.assertEqual(report["status"],"FAIL")
        self.assertFalse(report["service_identities_unchanged"])

    def test_launcher_error_never_falsely_passes(self):
        report=verify_dev_start(inspect=lambda:self.fixture(),
                                execute=lambda *a,**kw:SimpleNamespace(returncode=2))
        self.assertEqual(report["status"],"FAIL")
        self.assertEqual(report["launcher_exitcode"],2)

    def test_timeout_is_unknown_not_retry(self):
        from subprocess import TimeoutExpired
        calls=[]
        def execute(argv,**kwargs):
            calls.append(argv)
            raise TimeoutExpired(argv,90)
        report=verify_dev_start(inspect=lambda:self.fixture(),execute=execute)
        self.assertEqual(report["status"],"UNKNOWN")
        self.assertEqual(len(calls),1)


if __name__=="__main__":
    unittest.main()
