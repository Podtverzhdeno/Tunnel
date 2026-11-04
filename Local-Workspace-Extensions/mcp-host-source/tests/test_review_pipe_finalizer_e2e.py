"""P085 full offline transport -> authenticated QA -> durable DB merge E2E.

The Windows pipe/kernel is mocked because distinct OS accounts and protected
installation are unavailable in this workspace. The SQLite, QA/grant HMAC,
file journal and actual file promotion use *real disposable files*.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_review_finalizer import ProtectedFinalizerTests
from test_review_grant_broker_qa import QAKEY, POLICY
from workspace_mcp.review_finalizer import ProtectedReviewFinalizer
from workspace_mcp.review_pipe_server import serve_one


class PipeReviewE2ETests(unittest.TestCase):
    def setUp(self):
        self.fx=ProtectedFinalizerTests("test_full_pinned_journal_accept_no_candidate_exec")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.request=self.fx.q.request()
        self.kernel=Mock()
        self.kernel.ConnectNamedPipe.return_value=True
        self.kernel.SetNamedPipeHandleState.return_value=True

    def run_once(self, finalizer=None):
        broker=self.fx.f.broker
        with (patch("workspace_mcp.review_pipe_server._create_pipe",
                    return_value=(self.kernel,101)) as created,
              patch("workspace_mcp.review_pipe_server._read_message",
                    return_value=self.request),
              patch("workspace_mcp.review_pipe_server._read_exact",
                    return_value=b"\x06"),
              patch("workspace_mcp.review_pipe_server._write_message") as writer):
            answer=serve_one(self.fx.f.policy,broker,qa_policy=POLICY,
                             qa_verification_key=QAKEY,
                             trusted_finalizer=finalizer)
        self.assertEqual(created.call_count,1)
        self.assertEqual(writer.call_count,1)
        self.kernel.DisconnectNamedPipe.assert_called_once()
        self.kernel.CloseHandle.assert_called_once()
        return answer

    def test_full_accept_with_real_db_files_journal(self):
        answer=self.run_once(finalizer=self.fx.finalizer)
        self.assertTrue(answer["ok"],answer)
        self.assertEqual(answer["result"]["status"],"done")
        self.assertTrue(answer["delivery_acknowledged"])
        self.assertEqual(self.fx.state(),("done",8))
        self.assertFalse((self.fx.f.root/".agent-team/merge-intents/P152").exists())

    def test_no_privileged_finalizer_is_reservation_only(self):
        answer=self.run_once(finalizer=None)
        self.assertTrue(answer["ok"],answer)
        self.assertEqual(answer["result"]["state"],"RESERVED_REQUIRES_PRIVILEGED_QA")
        self.assertFalse(answer["result"]["source_merged"])
        self.assertEqual(self.fx.state(),("reviewing",7))
        self.assertFalse((self.fx.f.root/self.fx.f.source).exists())

    def test_crash_after_first_file_preserves_journal_and_not_done(self):
        class CrashAfterFirst(self.fx.journal):
            def apply(self):
                def fail(_):
                    raise RuntimeError("injected-crash-after-first-file")
                return super().apply(after_each=fail)
        finalizer=ProtectedReviewFinalizer(
            project=self.fx.f.root,database=self.fx.f.db,workspace="projects",
            owner_principals=self.fx.f.policy.owner_principals,
            trusted_reviewer_actor=self.fx.f.policy.reviewer_actor,
            ledger=self.fx.f.ledger,qa_key=QAKEY,qa_policy=POLICY,
            journal_factory=CrashAfterFirst)
        answer=self.run_once(finalizer=finalizer)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error_code"],"OWNER_RECONCILIATION_REQUIRED")
        self.assertEqual(self.fx.state(),("reviewing",7))
        self.assertTrue((self.fx.f.root/".agent-team/merge-intents/P152").is_dir())

    def test_wrong_qa_after_signed_packet_denied_with_no_files_merged(self):
        self.request["signed_qa_receipt"]["payload"]["checks"][0]["exit_code"]=1
        answer=self.run_once(finalizer=self.fx.finalizer)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["error_code"],"QA_SIGNATURE_INVALID")
        self.assertEqual(self.fx.state(),("reviewing",7))
        self.assertFalse((self.fx.f.root/self.fx.f.source).exists())

if __name__=="__main__":
    unittest.main()
