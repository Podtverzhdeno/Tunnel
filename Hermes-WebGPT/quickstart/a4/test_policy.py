"""A4 permission enforcement tests: all cases are offline and side-effect free."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quickstart.a4.policy import AccessDenied, Policy, READ_TOOLS, SCOPED_WRITES, NEVER_ALLOWED


class PolicyTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.scope = self.root / "allowed"
        self.scope.mkdir()
        (self.scope / "sub").mkdir()
        (self.root / "private.txt").write_text("private", encoding="utf-8")

    def policy(self, mode):
        return Policy(mode, "projects", self.root, self.scope)

    def check_denied(self, policy, name, args, reason=None):
        with self.assertRaises(AccessDenied) as err:
            policy.check(name, args)
        if reason is not None:
            self.assertEqual(str(err.exception), reason)

    def test_read_only_allows_explicit_reads_inside_scope(self):
        p = self.policy("read_only")
        for name in READ_TOOLS:
            args = {"workspace": "projects", "path": "allowed/sub"}
            if name == "search_text":
                args["query"] = "x"
            p.check(name, args)

    def test_read_only_blocks_every_mutating_tool(self):
        p = self.policy("read_only")
        for name in SCOPED_WRITES | NEVER_ALLOWED:
            self.check_denied(p, name, {
                "workspace": "projects", "path": "allowed/sub",
                "source": "allowed/sub", "destination": "allowed/sub",
            })

    def test_full_mode_allows_scoped_file_changes_but_not_unbounded_execution(self):
        p = self.policy("full_access")
        for name in SCOPED_WRITES:
            p.check(name, {"workspace": "projects", "path": "allowed/sub",
                           "source": "allowed/sub", "destination": "allowed/sub"})
        for name in NEVER_ALLOWED:
            self.check_denied(p, name, {"workspace": "projects", "path": "allowed/sub"})

    def test_scope_escape_and_wrong_workspace_are_denied(self):
        p = self.policy("full_access")
        for relative in ("private.txt", "../private.txt", "allowed/../private.txt",
                         r"C:\Windows\system.ini", r"\\server\share\a", "/etc/passwd"):
            self.check_denied(p, "read_file", {"workspace": "projects", "path": relative})
        self.check_denied(p, "read_file",
                          {"workspace": "other", "path": "allowed/sub"})

    def test_symlink_escape_denied(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        try:
            (self.scope / "jump").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted by OS")
        self.check_denied(self.policy("read_only"), "list_directory",
                          {"workspace": "projects", "path": "allowed/jump"})

    def test_move_requires_both_paths_within_scope(self):
        p = self.policy("full_access")
        self.check_denied(p, "move_path", {"workspace": "projects",
                           "source": "allowed/sub", "destination": "private.txt"})

    def test_unknown_tool_and_missing_workspace_denied(self):
        p = self.policy("read_only")
        self.check_denied(p, "unknown", {"workspace": "projects"})
        self.check_denied(p, "read_file", {"path": "allowed/sub"})

    def test_invalid_mode_and_scope_denied(self):
        with self.assertRaises(ValueError):
            self.policy("superadmin")
        with self.assertRaises(ValueError):
            Policy("read_only", "projects", self.root, self.root.parent)

    def test_git_index_refresh_and_outside_trash_are_denied(self):
        p = self.policy("full_access")
        for name in ("git_status", "git_diff", "git_log", "trash_path"):
            self.check_denied(p, name, {"workspace": "projects",
                                       "path": "allowed/sub"})

    def test_search_glob_must_remain_a_basename_pattern(self):
        p = self.policy("read_only")
        for glob in ("../*.key", r"..\*.key", "folder/*.py",
                     r"folder\*.py", "C:secret", ""):
            self.check_denied(p, "search_text", {
                "workspace": "projects", "path": "allowed/sub",
                "query": "needle", "filename_glob": glob},
                "unsafe_filename_glob")
        p.check("search_text", {"workspace": "projects", "path": "allowed/sub",
                               "query": "needle", "filename_glob": "*.py"})

    def test_immutable_policy(self):
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            self.policy("read_only").mode = "full_access"


if __name__ == "__main__":
    unittest.main()
