"""No-send, no-secret tests for authenticated Relay capacity reporting."""
import json
from pathlib import Path
import tempfile
import unittest

from quickstart.a4.relay_capacity_observer import capacity_report


class RelayCapacityObserverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "relay-auth.json"
        self.synthetic_token = "synthetic-runtime-secret-do-not-log-123456789"
        self.path.write_text(json.dumps({"key": self.synthetic_token}), encoding="utf8")

    def test_live_three_slots_uses_token_but_never_displays_it(self):
        calls = []
        def fetch(key):
            calls.append(key)
            return {"ok": True, "max_active_requests": 3,
                    "routes": {"extension": {"claimed": 1}},
                    "private_message": "not-to-be-printed"}
        outcome = capacity_report(auth_file=self.path, fetch=fetch)
        self.assertEqual(outcome["status"], "RUNNING_CAPACITY_CONFIRMED")
        self.assertEqual(outcome["running_relay_slots"], 3)
        self.assertTrue(outcome["at_least_three_slots_active"])
        self.assertFalse(outcome["three_parallel_chat_requests_e2e_verified"])
        self.assertFalse(outcome["bridge_send_verified"])
        self.assertEqual(calls, [self.synthetic_token])
        self.assertNotIn("synthetic-runtime-secret", str(outcome))
        self.assertNotIn("private_message", str(outcome))

    def test_one_slot_is_not_three_streams(self):
        out = capacity_report(auth_file=self.path, fetch=lambda _: {
            "ok": True, "max_active_requests": 1})
        self.assertTrue(out["local_relay_authenticated"])
        self.assertFalse(out["at_least_three_slots_active"])
        self.assertEqual(out["running_relay_slots"], 1)

    def test_missing_key_refuses_network(self):
        self.path.unlink()
        called = []
        out = capacity_report(auth_file=self.path,
                              fetch=lambda _: called.append(True))
        self.assertEqual(out["status"], "AUTH_FILE_UNAVAILABLE")
        self.assertFalse(called)

    def test_too_short_key_refuses_network(self):
        self.path.write_text(json.dumps({"key": "short"}), encoding="utf8")
        out = capacity_report(auth_file=self.path, fetch=lambda _: 1)
        self.assertEqual(out["status"], "AUTH_FILE_INVALID")

    def test_unknown_response_or_boolean_count_not_accepted(self):
        for item in (
            {"ok": True, "max_active_requests": True},
            {"ok": True, "max_active_requests": "3"},
            {"ok": False, "max_active_requests": 3},
            {"ok": True, "max_active_requests": 5000},
        ):
            with self.subTest(response=item):
                out = capacity_report(auth_file=self.path, fetch=lambda _: item)
                self.assertEqual(out["status"], "RELAY_UNEXPECTED_RESPONSE")
                self.assertFalse(out["local_relay_authenticated"])

    def test_exception_does_not_reveal_key(self):
        def fetch(key):
            raise OSError("private-error:" + key)
        out = capacity_report(auth_file=self.path, fetch=fetch)
        self.assertEqual(out["status"], "RELAY_STATUS_UNAVAILABLE")
        self.assertNotIn(self.synthetic_token, str(out))

    def test_symlinked_key_refused_when_supported(self):
        other = self.path.with_name("source.json")
        self.path.replace(other)
        try:
            self.path.symlink_to(other)
        except (NotImplementedError, OSError):
            self.skipTest("symlink permission unavailable")
        out = capacity_report(auth_file=self.path, fetch=lambda _: {
            "ok": True, "max_active_requests": 3})
        self.assertEqual(out["status"], "AUTH_FILE_UNAVAILABLE")

if __name__ == "__main__":
    unittest.main()
