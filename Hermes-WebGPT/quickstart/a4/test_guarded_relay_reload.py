"""One-click Relay-reload decision tests, no process termination."""
import unittest
from unittest.mock import patch
from quickstart.a4 import guarded_relay_reload as app


class GuardedReloadDecisionTests(unittest.TestCase):
    def test_up_to_date_runtime_is_not_restarted_even_with_apply(self):
        with patch.object(app, "plan", return_value={
            "reconciliation": "NOT_REQUIRED", "source_changed_since_load": []}), \
             patch.object(app, "_pinned_sources_attested",
                          side_effect=AssertionError("no archive check needed")):
            out = app.guarded_once(apply=True)
        self.assertFalse(out["reload_attempted"])
        self.assertEqual(out["reload_outcome"], "NOT_ATTEMPTED")

    def test_missing_idle_guard_never_calls_start(self):
        with patch.object(app, "plan", return_value={
            "reconciliation": "PREFLIGHT_BLOCKED",
            "source_changed_since_load": ["relay.py"]}), \
             patch.object(app, "_pinned_sources_attested",
                          side_effect=AssertionError("not safe")):
            out = app.guarded_once(apply=True)
        self.assertFalse(out["reload_attempted"])
        self.assertEqual(out["preflight"], "PREFLIGHT_BLOCKED")

    def test_archive_hash_drift_blocks_without_process_changes(self):
        with patch.object(app, "plan", return_value={
            "reconciliation": "READY_FOR_EXPLICIT_RELOAD",
            "source_changed_since_load": ["relay.py"]}), \
             patch.object(app, "_pinned_sources_attested", return_value=False):
            out = app.guarded_once(apply=True)
        self.assertEqual(out["preflight"], "CANDIDATE_SOURCE_DRIFT")
        self.assertFalse(out["reload_attempted"])

    def test_readonly_approval_never_triggers_guard_apply(self):
        with patch.object(app, "plan", return_value={
            "reconciliation": "READY_FOR_EXPLICIT_RELOAD",
            "source_changed_since_load": ["relay.py"]}), \
             patch.object(app, "_pinned_sources_attested", return_value=True), \
             patch.object(app.importlib.util, "spec_from_file_location",
                          side_effect=AssertionError("no import of restart executor")):
            out = app.guarded_once(apply=False)
        self.assertEqual(out["reload_outcome"], "READY_ONLY")
        self.assertTrue(out["candidate_sources_pinned"])
        self.assertFalse(out["reload_attempted"])


if __name__ == "__main__":
    unittest.main()
