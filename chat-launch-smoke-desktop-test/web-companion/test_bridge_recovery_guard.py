"""Offline regression tests: an unverified browser identity must block recovery.

All fixtures live in a temporary directory. No browser, bridge server, network,
queue admissions, credentials, or ChatGPT send calls are involved.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge


JOB_ID = "01234567-89ab-4cde-8901-000000000201"


class BrowserRecoveryGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.active = self.root / "active"
        self.diag = self.root / "diagnostics"
        self.active.mkdir()
        self.diag.mkdir()
        for name, value in (("ACTIVE", self.active), ("DIAG", self.diag)):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        record = {"id": JOB_ID, "completion_mode": "report",
                  "report_name": "completion-" + JOB_ID + ".md"}
        (self.active / (JOB_ID + ".json")).write_text(
            json.dumps(record), encoding="utf-8"
        )

    def decision(self, *stages):
        events = [{"stage": stage} for stage in stages]
        (self.diag / (JOB_ID + ".json")).write_text(
            json.dumps(events), encoding="utf-8"
        )
        return bridge.recovery_candidate(JOB_ID)

    def test_send_and_saved_with_no_security_incident_can_reattach(self):
        self.assertEqual(self.decision("send_clicked", "message_saved"),
                         (True, "eligible"))

    def test_unverified_tab_identity_blocks_recovery(self):
        self.assertEqual(
            self.decision("send_clicked", "message_saved",
                          "tab_identity_unverified"),
            (False, "needs_reconciliation")
        )

    def test_changed_auth_context_blocks_recovery(self):
        self.assertEqual(
            self.decision("send_clicked", "message_saved",
                          "auth_context_changed"),
            (False, "needs_reconciliation")
        )

    def test_existing_tab_binding_dispute_still_blocks(self):
        self.assertEqual(
            self.decision("send_clicked", "message_saved",
                          "tab_binding_disputed"),
            (False, "needs_reconciliation")
        )

    def test_no_send_confirmation_never_recovers(self):
        self.assertEqual(self.decision("message_saved"),
                         (False, "missing_evidence"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
