"""A4 observer-config integration. The installed host is optional in CI."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from quickstart.a4.observer_profile import (ObserverConfigError, observer_config,
                                            prepare)

HOST = Path(__file__).resolve().parents[3] / "Local-Workspace-Extensions" / "mcp-host-source"


class ObserverPreparationTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.workspace = self.base / "workspace"
        self.project = self.workspace / "Hermes-WebGPT"
        self.project.mkdir(parents=True)
        browser = self.workspace / "YandexBrowser" / "Application" / "browser.exe"
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"test")
        self.settings = {
            "schema_version": 1, "mode": "read_only", "workers": 3,
            "scope": str(self.project), "browser": "yandex",
            "browser_exe": str(browser),
            "enforcement": "pending_backend_integration",
        }
        self.state = self.base / "appdata"
        self.state.mkdir()
        self.destination = self.state / "observer-config.json"

    def test_create_idempotent_and_nongranting(self):
        one = prepare(self.settings, self.project, self.destination)
        self.assertEqual(one["state"], "prepared")
        again = prepare(self.settings, self.project, self.destination)
        self.assertEqual(again["state"], "already_prepared")
        self.assertFalse(again["write"])
        item = json.loads(self.destination.read_text(encoding="utf-8"))["roots"][0]
        self.assertEqual(item["name"], "hermes")
        self.assertEqual((item["write"], item["execute"], item["git"]),
                         (False, False, False))

    def test_full_access_cannot_prepare_observer_by_relabeling(self):
        cfg = dict(self.settings, mode="full_access")
        with self.assertRaises(ObserverConfigError):
            prepare(cfg, self.project, self.destination)
        self.assertFalse(self.destination.exists())

    def test_config_inside_visible_workspace_is_denied(self):
        inside = self.project / "config.json"
        with self.assertRaises(ObserverConfigError):
            prepare(self.settings, self.project, inside)
        self.assertFalse(inside.exists())

    def test_different_existing_scope_requires_manual_reconciliation(self):
        self.destination.write_text('{"roots":[]}', encoding="utf-8")
        with self.assertRaises(ObserverConfigError):
            prepare(self.settings, self.project, self.destination)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), '{"roots":[]}')

    @unittest.skipUnless((HOST / "workspace_mcp" / "tools.py").is_file(),
                         "Local Workspace native observer source not available")
    def test_generated_config_is_actually_readonly_in_installed_http_host(self):
        prepare(self.settings, self.project, self.destination)
        sys.path.insert(0, str(HOST))
        try:
            from workspace_mcp.config import Settings, WorkspaceError
            from workspace_mcp.tools import ToolRegistry, OBSERVATION_TOOLS
            registry = ToolRegistry(Settings(config_file=self.destination,
                                             state_dir=self.state / "host-state"),
                                    profile="observer")
            self.assertEqual({x["name"] for x in registry.list()}, OBSERVATION_TOOLS)
            roots = registry.call("list_workspaces", {})
            self.assertEqual(len(roots), 1)
            self.assertFalse(roots[0]["write"])
            self.assertFalse(roots[0]["execute"])
            with self.assertRaises(WorkspaceError):
                registry.call("write_file", {"workspace":"hermes",
                                             "path":"bad", "content":"bad"})
        finally:
            sys.path.remove(str(HOST))


if __name__ == "__main__":
    unittest.main()
