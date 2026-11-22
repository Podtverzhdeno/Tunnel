"""Offline safety contract for the existing Relay admission cap.

No Chrome, Telegram, network or external effects. Exercises the *real* SQLite
Queue, not a mocked acceptance algorithm; proves source-level limits only.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

# Import the existing Relay without changing its source or packaging.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from relay import MODEL, Queue, RelayError


def request(number):
    return {"model": MODEL, "messages": [
        {"role": "user", "content": f"synthetic capacity item {number}"}
    ]}


def turn(number):
    return f"synthetic-owner-turn-{number:05}"


class RelayAdmissionSafetyContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.directory.name) / "relay-contract.sqlite")

    def tearDown(self):
        try:
            self.queue.close()
        finally:
            self.directory.cleanup()

    def submit(self, number, *, transport="extension"):
        return self.queue.submit(request(number),
                                 transport=transport, turn_id=turn(number))

    def rows(self):
        return self.queue.db.execute(
            "SELECT id,state FROM requests ORDER BY id").fetchall()

    def test_thirty_concurrent_distinct_turns_admit_five_only(self):
        def work(i):
            try:
                return ("admitted", i, self.submit(i))
            except RelayError as exc:
                return ("rejected", i, str(exc))

        # No consumer drains the five admission slots until all 30 attempted.
        # Their actual thread interleaving does not affect the DB invariant.
        with ThreadPoolExecutor(max_workers=30) as pool:
            output = list(pool.map(work, range(30)))
        admitted = [x for x in output if x[0] == "admitted"]
        rejected = [x for x in output if x[0] == "rejected"]
        self.assertEqual(len(admitted), 5)
        self.assertEqual(len(rejected), 25)
        self.assertTrue(all(x[2] == "queue_full" for x in rejected))
        self.assertEqual(len({x[2] for x in admitted}), 5)
        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM turn_bindings").fetchone()[0], 5)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE state!='queued'").fetchone()[0], 0)

    def test_repeated_existing_turn_reattaches_even_when_full(self):
        accepted = [self.submit(i) for i in range(5)]
        self.assertEqual(self.submit(0), accepted[0])
        self.assertEqual(len(self.rows()), 5)
        with self.assertRaisesRegex(RelayError, "^turn_payload_conflict$"):
            self.queue.submit(request(99), turn_id=turn(0))
        with self.assertRaisesRegex(RelayError, "^transport_switch_forbidden$"):
            self.queue.submit(request(0), transport="playwright",
                              turn_id=turn(0))
        self.assertEqual(len(self.rows()), 5)

    def test_rejected_new_turn_creates_no_request_or_binding(self):
        for i in range(5):
            self.submit(i)
        with self.assertRaisesRegex(RelayError, "^queue_full$"):
            self.submit(50)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM turn_bindings WHERE turn_id=?",
            (turn(50),)).fetchone()[0], 0)
        self.assertEqual(len(self.rows()), 5)

    def test_only_one_global_claim_even_with_multiple_workers(self):
        for i in range(5):
            self.submit(i)
        first = self.queue.claim(str(uuid.uuid4()))
        self.assertIsNotNone(first)
        self.assertEqual(first["transport"], "extension")
        self.assertIsNone(self.queue.claim(str(uuid.uuid4())))
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE state='claimed'").fetchone()[0], 1)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE state='queued'").fetchone()[0], 4)

    def test_terminal_pre_effect_cancel_reopens_only_one_slot(self):
        accepted = [self.submit(i) for i in range(5)]
        self.assertEqual(self.queue.cancel(accepted[0]), "blocked")
        next_id = self.submit(50)
        self.assertNotIn(next_id, accepted)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE state NOT IN ('done','blocked','uncertain')"
        ).fetchone()[0], 5)
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE state='blocked'"
        ).fetchone()[0], 1)
        self.assertIsNone(self.queue.db.execute(
            "SELECT result FROM requests WHERE id=?", (next_id,)
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
