"""A4 development launcher integrates the original guarded Web Companion Bridge.

These tests never start the Bridge; real idempotence is separately tested by
verify_default_dev_cmd.py on the configured Windows host.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DevCompanionBootstrapTests(unittest.TestCase):
    def test_dev_bridge_reuses_a1_guard_not_raw_bridge_source(self):
        script = (ROOT / "quickstart" / "start.ps1").read_text(encoding="utf-8-sig")
        anchor = 'if ($DevMode) {\n        # Reuse the existing A1-owned guarded Bridge launcher.'
        self.assertIn(anchor, script)
        dev_area = script[script.index(anchor):]
        self.assertIn("guarded_start_companion_bridge_v2.py", dev_area)
        self.assertIn("ALREADY_RUNNING_VERIFIED", dev_area)
        self.assertIn("STARTED_VERIFIED", dev_area)
        self.assertIn("WEB_COMPANION_BRIDGE_AUTH_OK", dev_area)
        self.assertNotIn("bridge.py", dev_area)
        self.assertNotIn("rm -rf", dev_area)
        self.assertNotIn("queue/", dev_area)

    def test_bridge_not_started_from_dry_run_or_strict_launch(self):
        script = (ROOT / "quickstart" / "start.ps1").read_text(encoding="utf-8-sig")
        # Dry-run exits before the guarded Bridge startup code.
        self.assertLess(script.index("if ($DryRun) {"),
                        script.index("guarded_start_companion_bridge_v2.py"))
        guard_section = script[script.index("guarded_start_companion_bridge_v2.py")-350:]
        self.assertIn("if ($DevMode) {", guard_section[:350])
        # Errors in the optional Bridge are warnings, never false ready.
        self.assertIn("guarded start", guard_section)
        self.assertIn("не подтверждено", guard_section)

    def test_messaging_is_not_reported_verified_by_bridge_health(self):
        script = (ROOT / "quickstart" / "start.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("AUTOPILOT_SEND_UNVERIFIED", script)
        self.assertIn("prompt_dispatched -ne $true", script)
        self.assertIn("yandex_handoff_readiness.py", script)
        self.assertIn("127.0.0.1", (ROOT / "quickstart/a4/yandex_handoff_readiness.py").read_text(encoding="utf8"))


if __name__ == "__main__":
    unittest.main()
