"""Hermes WebGPT Relay admission tests: no provider, browser, or credentials.

A 50-agent retry burst must not create unbounded HTTP subscribers, alter
an existing logical turn, or leave rejected new requests in durable queue.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
import threading
import unittest

import relay


def request(message="fixture"):
    return {"model": relay.MODEL, "messages": [{"role": "user", "content": message}]}


class WaiterBudgetTests(unittest.TestCase):
    def setUp(self):
        self.queue = relay.Queue(":memory:")
        self.server = relay.Server(
            ("127.0.0.1", 0), self.queue, "synthetic-only-key",
            timeout=2, max_http_waiters=3, max_waiters_per_turn=2)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.server_close()
        self.queue.close()

    def test_validated_resource_limits(self):
        for kwargs in (
            {"max_http_waiters": 0},
            {"max_http_waiters": True},
            {"max_http_waiters": 1001},
            {"max_http_waiters": 3, "max_waiters_per_turn": 4},
            {"max_http_waiters": 3, "max_waiters_per_turn": False},
            {"max_http_waiters": 3, "max_waiters_per_turn": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                    relay.RelayError, "invalid_waiter_budget"):
                relay.Server(("127.0.0.1", 0), self.queue, "synthetic-only-key", **kwargs)

    def test_same_turn_dedup_and_rejection_does_not_kill_current_waiters(self):
        turn = "turn-id-fixture-0001"
        first = self.server.submit_and_attach(request(), turn_id=turn)
        second = self.server.submit_and_attach(request(), turn_id=turn)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(relay.RelayError, "turn_waiter_capacity"):
            self.server.submit_and_attach(request(), turn_id=turn)
        self.assertEqual(self.server.waiter_count(first), 2)
        self.assertEqual(self.queue.read(first)["state"], "queued")
        self.assertEqual(self.queue.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.server.detach_waiter(first)
        self.assertEqual(first, self.server.submit_and_attach(request(), turn_id=turn))
        self.assertEqual(self.server.waiter_count(first), 2)
        self.server.detach_waiter(first)
        self.server.detach_waiter(first, on_last="cancel")
        self.assertEqual(self.queue.read(first)["state"], "blocked")

    def test_global_limit_rejects_before_creating_new_request(self):
        ids = [
            self.server.submit_and_attach(request(), turn_id="turn-id-fixture-%04d" % i)
            for i in range(2)
        ]
        third = "turn-id-fixture-0003"
        self.server.submit_and_attach(request(), turn_id="turn-id-fixture-0002")
        self.assertEqual(self.server.active_waiter_count(), 3)
        with self.assertRaisesRegex(relay.RelayError, "http_waiter_capacity"):
            self.server.submit_and_attach(request("different"), turn_id=third)
        self.assertEqual(self.queue.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 2)
        self.assertIsNone(self.queue.db.execute(
            "SELECT request_id FROM turn_bindings WHERE turn_id=?", (third,)).fetchone())
        self.server.detach_waiter(ids[1])
        new_id = self.server.submit_and_attach(request("different"), turn_id=third)
        self.assertEqual(self.queue.read(new_id)["state"], "queued")
        self.assertEqual(self.server.active_waiter_count(), 3)
        self.server.detach_waiter(ids[0], on_last="cancel")
        self.server.detach_waiter(ids[1], on_last="cancel")
        self.server.detach_waiter(new_id, on_last="cancel")

    def test_50_parallel_subscribers_same_turn_bounded(self):
        turn = "turn-id-fixture-0050"
        attempts = []
        def submit(_):
            try:
                return ("ok", self.server.submit_and_attach(request(), turn_id=turn))
            except relay.RelayError as exc:
                return ("rejected", str(exc))
        with ThreadPoolExecutor(max_workers=50) as pool:
            attempts = list(pool.map(submit, range(50)))
        accepted = [v for kind, v in attempts if kind == "ok"]
        rejected = [v for kind, v in attempts if kind == "rejected"]
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(set(accepted)), 1)
        self.assertEqual(rejected, ["turn_waiter_capacity"] * 48)
        self.assertEqual(self.server.active_waiter_count(), 2)
        self.assertEqual(self.queue.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.server.detach_waiter(accepted[0])
        self.server.detach_waiter(accepted[1], on_last="cancel")

    def test_reject_does_not_mutate_claimed_or_uncertain_state(self):
        turn = "turn-id-fixture-0008"
        first = self.server.submit_and_attach(request(), turn_id=turn)
        second = self.server.submit_and_attach(request(), turn_id=turn)
        job = self.queue.claim("00000000-0000-4000-8000-000000000001")
        self.assertIsNotNone(job)
        self.queue.event(first, job["lease"], "intent", tab=7)
        # A refused extra HTTP watcher may NOT cancel or replay a click intent.
        with self.assertRaisesRegex(relay.RelayError, "turn_waiter_capacity"):
            self.server.submit_and_attach(request(), turn_id=turn)
        self.assertEqual(self.queue.read(first)["state"], "intent")
        self.server.detach_waiter(first)
        self.server.detach_waiter(second, on_last="cancel")
        self.assertEqual(self.queue.read(first)["state"], "uncertain")

    def test_http_status_backpressure_has_retry_after_and_no_orphan(self):
        server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        server_thread.start()
        try:
            ids = [
                self.server.submit_and_attach(request(), turn_id="turn-id-fixture-%04d" % i)
                for i in range(3)
            ]
            with closing(http.client.HTTPConnection(*self.server.server_address, timeout=2)) as conn:
                conn.request(
                    "POST", "/v1/chat/completions", body=json.dumps(request("fourth")),
                    headers={"Authorization":"Bearer synthetic-only-key",
                             "Content-Type":"application/json"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 429)
                self.assertEqual(resp.getheader("Retry-After"), "1")
                self.assertEqual(json.loads(resp.read())["error"]["code"], "http_waiter_capacity")
            self.assertEqual(self.queue.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 3)
            for ident in ids:
                self.server.detach_waiter(ident, on_last="cancel" if ident == ids[-1] else None)
        finally:
            self.server.shutdown()
            server_thread.join(timeout=3)
            self.assertFalse(server_thread.is_alive())

    def test_status_reports_actual_waiter_budget(self):
        self.server.submit_and_attach(request(), turn_id="turn-id-fixture-0001")
        t=threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        try:
            with closing(http.client.HTTPConnection(*self.server.server_address, timeout=2)) as conn:
                conn.request("GET", "/bridge/status",
                             headers={"Authorization": "Bearer synthetic-only-key"})
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                d=json.loads(response.read())
            self.assertEqual(d["active_http_waiters"], 1)
            self.assertEqual(d["max_http_waiters"], 3)
            self.assertEqual(d["max_waiters_per_turn"], 2)
            self.assertEqual(d["max_active_requests"], 1)
        finally:
            self.server.shutdown()
            t.join(timeout=3)
            self.server.detach_waiter(self.queue.db.execute(
                "SELECT id FROM requests LIMIT 1").fetchone()[0],on_last="cancel")


if __name__ == "__main__":
    unittest.main()
