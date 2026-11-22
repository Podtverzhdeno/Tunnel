"""Offline, real-SQLite crash/replay tests; never drives a ChatGPT browser."""
import concurrent.futures
import secrets
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from temporary_chat_ledger import (
    SessionConflict, TEMPORARY_URL, TemporaryChatLedger,
)


def new_uuid():
    return str(uuid.uuid4())


class TemporaryChatLedgerTests(unittest.TestCase):
    def setUp(self):
        self.working = tempfile.TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.path = Path(self.working.name) / "private-session-ledger.sqlite"
        self.store = TemporaryChatLedger(self.path)
        self.sid = new_uuid()
        self.profile = secrets.token_hex(16)
        self.epoch = new_uuid()
        self.tab = 151
        self.window = 33
        self.store.create_session(self.sid, self.profile)
        self.store.bind_tab(self.sid, self.tab, self.window,
                            self.epoch, TEMPORARY_URL, self.profile)

    def verify(self):
        self.store.attest_ui(
            self.sid, self.tab, self.epoch, self.profile,
            ui_verified=True, proof_nonce=secrets.token_hex(16)
        )

    def test_temp_mode_must_be_observed_not_inferred_from_url(self):
        rid = new_uuid()
        with self.assertRaisesRegex(SessionConflict, "session_state_conflict"):
            self.store.prepare_request(self.sid, rid, self.tab,
                                       self.epoch, self.profile)
        with self.assertRaisesRegex(SessionConflict, "temporary_ui_not_verified"):
            self.store.attest_ui(
                self.sid, self.tab, self.epoch, self.profile,
                ui_verified=False, proof_nonce=secrets.token_hex(16))
        self.assertEqual(self.store.status(self.sid)["state"], "ATTACHED")

    def test_true_request_lifecycle_and_fresh_proof_per_turn(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        grant = self.store.arm_one_send(self.sid, rid, self.tab,
                                        self.epoch, self.profile)
        self.assertEqual(grant["authorization"], "ONE_CLICK_ONLY")
        with self.assertRaises(SessionConflict):
            self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.observe_user_turn(self.sid, rid, self.tab, self.epoch,
                                     self.profile, secrets.token_hex(16))
        self.store.observe_response(self.sid, rid, self.tab, self.epoch,
                                    self.profile, secrets.token_hex(32))
        self.store.acknowledge_result(self.sid, rid, self.tab, self.epoch,
                                      self.profile)
        snap = self.store.status(self.sid)
        self.assertEqual(snap["state"], "AWAITING_TEMP_RECHECK")
        self.assertEqual(snap["requests"][0]["state"], "ACKED")
        next_id = new_uuid()
        with self.assertRaises(SessionConflict):
            self.store.prepare_request(self.sid, next_id, self.tab, self.epoch,
                                       self.profile)
        self.verify()
        self.store.prepare_request(self.sid, next_id, self.tab, self.epoch,
                                   self.profile)
        self.store.arm_one_send(self.sid, next_id, self.tab, self.epoch,
                                self.profile)
        self.assertEqual(len(self.store.status(self.sid)["requests"]), 2)

    def test_browser_loss_after_arm_requires_reconciliation(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.pause_if_browser_lost(self.sid, reason="browser_closed")
        fresh = TemporaryChatLedger(self.path)
        self.assertEqual(
            fresh.status(self.sid)["requests"][0]["state"], "UNCERTAIN_NO_REPLAY")
        self.assertEqual(fresh.status(self.sid)["state"],
                         "PAUSED_NEEDS_RECONCILIATION")
        with self.assertRaises(SessionConflict):
            fresh.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)
        with self.assertRaises(SessionConflict):
            fresh.prepare_request(self.sid, new_uuid(), self.tab,
                                  self.epoch, self.profile)

    def test_prepared_request_abandoned_does_not_become_send_grant(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.pause_if_browser_lost(self.sid, reason="worker_crashed")
        self.assertEqual(
            self.store.status(self.sid)["requests"][0]["state"],
            "ABANDONED_PRE_SEND")
        with self.assertRaises(SessionConflict):
            self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)

    def test_profile_tab_and_document_epoch_are_strictly_bound(self):
        self.verify()
        rid = new_uuid()
        for tab, epoch, profile in [
            (self.tab + 1, self.epoch, self.profile),
            (self.tab, new_uuid(), self.profile),
            (self.tab, self.epoch, secrets.token_hex(16)),
        ]:
            with self.assertRaises(SessionConflict):
                self.store.prepare_request(self.sid, rid, tab, epoch, profile)
        self.assertEqual(self.store.status(self.sid)["state"], "TEMP_VERIFIED")

    def test_one_tab_cannot_be_owned_by_two_sessions(self):
        second_sid = new_uuid()
        self.store.create_session(second_sid, self.profile)
        with self.assertRaisesRegex(SessionConflict, "browser_tab_already_owned"):
            self.store.bind_tab(second_sid, self.tab, self.window, new_uuid(),
                                TEMPORARY_URL, self.profile)
        self.assertEqual(self.store.status(second_sid)["state"], "CREATED")
        # A distinct tab in the same browser profile is eligible.
        self.store.bind_tab(second_sid, self.tab + 1, self.window, new_uuid(),
                            TEMPORARY_URL, self.profile)
        self.assertEqual(self.store.status(second_sid)["state"], "ATTACHED")

    def test_paused_tab_can_only_be_released_by_explicit_owner_and_no_replay(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)
        self.store.pause_if_browser_lost(self.sid, reason="browser_closed")
        with self.assertRaisesRegex(SessionConflict, "explicit_owner_release_required"):
            self.store.archive_paused_session(
                self.sid, self.profile, self.tab, self.epoch, user_approved=False)
        with self.assertRaisesRegex(SessionConflict, "wrong_browser_profile"):
            self.store.archive_paused_session(
                self.sid, secrets.token_hex(16), self.tab, self.epoch,
                user_approved=True)
        self.store.archive_paused_session(
            self.sid, self.profile, self.tab, self.epoch, user_approved=True)
        snap = TemporaryChatLedger(self.path).status(self.sid)
        self.assertEqual(snap["state"], "ARCHIVED")
        self.assertIsNone(snap["tab_id"])
        self.assertEqual(snap["requests"][0]["state"], "UNCERTAIN_NO_REPLAY")
        with self.assertRaises(SessionConflict):
            self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)
        successor = new_uuid()
        self.store.create_session(successor, self.profile)
        # The same numeric browser tab ID may legitimately reappear later; it
        # is not reusable until all old rights have been explicitly archived.
        self.store.bind_tab(successor, self.tab, self.window, new_uuid(),
                            TEMPORARY_URL, self.profile)
        self.assertEqual(self.store.status(successor)["state"], "ATTACHED")

    def test_request_uuid_cannot_be_reused_across_sessions(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        second_sid, second_epoch, second_profile = new_uuid(), new_uuid(), secrets.token_hex(16)
        self.store.create_session(second_sid, second_profile)
        self.store.bind_tab(second_sid, 201, 40, second_epoch,
                            TEMPORARY_URL, second_profile)
        self.store.attest_ui(second_sid, 201, second_epoch, second_profile,
                             ui_verified=True, proof_nonce=secrets.token_hex(16))
        with self.assertRaisesRegex(SessionConflict, "request_id_already_used_no_replay"):
            self.store.prepare_request(second_sid, rid, 201, second_epoch, second_profile)

    def test_stale_ui_proof_requires_refresh_before_arming(self):
        with patch("temporary_chat_ledger.time.time", return_value=10000):
            self.verify()
            rid = new_uuid()
            self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        with patch("temporary_chat_ledger.time.time", return_value=10011):
            with self.assertRaisesRegex(SessionConflict,
                                        "stale_temporary_ui_attestation"):
                self.store.arm_one_send(self.sid, rid, self.tab, self.epoch,
                                        self.profile)
            self.store.attest_ui(self.sid, self.tab, self.epoch, self.profile,
                                 ui_verified=True, proof_nonce=secrets.token_hex(16))
            self.store.arm_one_send(self.sid, rid, self.tab, self.epoch, self.profile)

    def test_two_contending_workers_get_only_one_send_grant(self):
        self.verify()
        rid = new_uuid()
        self.store.prepare_request(self.sid, rid, self.tab, self.epoch, self.profile)
        workers = [TemporaryChatLedger(self.path), TemporaryChatLedger(self.path)]
        def attempt(worker):
            try:
                return worker.arm_one_send(self.sid, rid, self.tab,
                                           self.epoch, self.profile)["authorization"]
            except SessionConflict:
                return "DENIED"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, workers))
        self.assertEqual(sorted(results), ["DENIED", "ONE_CLICK_ONLY"])

    def test_no_prompt_transcript_cookies_or_account_stored(self):
        with sqlite3.connect(self.path) as con:
            columns = [
                row[1] for table in ("request", "session")
                for row in con.execute("PRAGMA table_info(" + table + ")")
            ]
        forbidden = ("prompt", "message", "answer", "text", "cookie", "email",
                     "account", "token", "content", "chat_history")
        self.assertTrue(all(word not in col.lower()
                            for col in columns for word in forbidden))
        self.assertNotIn("profile_nonce", self.store.status(self.sid))

    def test_replayed_ui_proof_and_response_receipt_never_authorize_result(self):
        ui_proof = "a" * 32
        user_evidence = "b" * 32
        result_receipt = "c" * 64
        self.store.attest_ui(self.sid, self.tab, self.epoch, self.profile,
                             ui_verified=True, proof_nonce=ui_proof)
        first = new_uuid()
        self.store.prepare_request(self.sid, first, self.tab, self.epoch, self.profile)
        self.store.arm_one_send(self.sid, first, self.tab, self.epoch, self.profile)
        self.store.observe_user_turn(self.sid, first, self.tab, self.epoch,
                                     self.profile, user_evidence)
        self.store.observe_response(self.sid, first, self.tab, self.epoch,
                                    self.profile, result_receipt)
        self.store.acknowledge_result(self.sid, first, self.tab, self.epoch, self.profile)
        with self.assertRaisesRegex(SessionConflict, "ui_attestation_nonce_replayed"):
            self.store.attest_ui(self.sid, self.tab, self.epoch, self.profile,
                                 ui_verified=True, proof_nonce=ui_proof)
        self.assertEqual(self.store.status(self.sid)["state"], "AWAITING_TEMP_RECHECK")
        self.verify()
        second = new_uuid()
        self.store.prepare_request(self.sid, second, self.tab, self.epoch, self.profile)
        self.store.arm_one_send(self.sid, second, self.tab, self.epoch, self.profile)
        with self.assertRaisesRegex(SessionConflict, "user_evidence_replayed"):
            self.store.observe_user_turn(self.sid, second, self.tab, self.epoch,
                                         self.profile, user_evidence)
        self.store.observe_user_turn(self.sid, second, self.tab, self.epoch,
                                     self.profile, secrets.token_hex(16))
        with self.assertRaisesRegex(SessionConflict, "response_receipt_replayed"):
            self.store.observe_response(self.sid, second, self.tab, self.epoch,
                                        self.profile, result_receipt)
        self.store.observe_response(self.sid, second, self.tab, self.epoch,
                                    self.profile, secrets.token_hex(32))
        self.store.acknowledge_result(self.sid, second, self.tab, self.epoch,
                                      self.profile)

    def test_rejects_noncanonical_id_and_invalid_pause_reason(self):
        with self.assertRaisesRegex(SessionConflict, "noncanonical_uuid"):
            self.store.status(self.sid.upper())
        with self.assertRaises(SessionConflict):
            self.store.pause_if_browser_lost(self.sid, reason="just_try_again")
        self.assertEqual(self.store.status(self.sid)["state"], "ATTACHED")


if __name__ == "__main__":
    unittest.main()
