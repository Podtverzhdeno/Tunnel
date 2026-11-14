"""Fail-closed, project-scoped Hermes tool permission policy (A4).

IMPORTANT: This module must sit on the actual tool-call boundary. Saving a
permission choice does NOT constrain a running, unguarded MCP server.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Mapping, Any

READ_TOOLS = frozenset({
    "list_directory", "directory_tree", "file_info", "read_file",
    "read_binary", "search_text", "git_status", "git_diff", "git_log",
})
SCOPED_WRITES = frozenset({
    "create_directory", "write_file", "replace_text", "move_path",
    "trash_path",
})
# Executing code (even 'tests'), pushing Git, and arbitrary commands can write
# outside a workspace. They require a separate OS-enforced sandbox, not a mode.
NEVER_ALLOWED = frozenset({
    "run_command", "run_script", "run_tests", "git_push", "git_fetch",
    "git_add", "git_commit", "cancel_job", "restore_trash",
})
PATH_FIELDS = {
    "list_directory": ("path",),
    "directory_tree": ("path",),
    "file_info": ("path",),
    "read_file": ("path",),
    "read_binary": ("path",),
    "search_text": ("path",),
    "git_status": ("path",),
    "git_diff": ("path",),
    "git_log": ("path",),
    "create_directory": ("path",),
    "write_file": ("path",),
    "replace_text": ("path",),
    "move_path": ("source", "destination"),
    "trash_path": ("path",),
}


class AccessDenied(PermissionError):
    """Denied without executing the underlying tool."""


@dataclass(frozen=True)
class Policy:
    mode: str
    workspace_alias: str
    workspace_root: Path
    scope: Path

    def __post_init__(self):
        if self.mode not in ("read_only", "full_access"):
            raise ValueError("invalid_permission_mode")
        if not self.workspace_alias or self.workspace_alias.strip() != self.workspace_alias:
            raise ValueError("invalid_workspace")
        root = self.workspace_root.resolve(strict=True)
        scope = self.scope.resolve(strict=True)
        if not scope.is_dir() or not scope.is_relative_to(root):
            raise ValueError("scope_outside_workspace")
        object.__setattr__(self, "workspace_root", root)
        object.__setattr__(self, "scope", scope)

    def check(self, tool: str, args: Mapping[str, Any]) -> None:
        """Check before dispatch. Denies unknown tools and unbounded paths."""
        name = tool.removeprefix("mcp__Local_Workspace__")
        if name in NEVER_ALLOWED or name not in READ_TOOLS | SCOPED_WRITES:
            raise AccessDenied("tool_not_permitted")
        if self.mode == "read_only" and name not in READ_TOOLS:
            raise AccessDenied("read_only_denies_writes")
        if not isinstance(args, Mapping) or args.get("workspace") != self.workspace_alias:
            raise AccessDenied("workspace_not_authorized")
        for field in PATH_FIELDS[name]:
            relative = args.get(field, "")
            if not isinstance(relative, str):
                raise AccessDenied("invalid_path")
            # Explicitly deny absolute paths, drive prefixes, UNC paths and
            # traversal before .resolve(), even if traversal ends in scope.
            win = PureWindowsPath(relative)
            if win.is_absolute() or win.drive or ".." in win.parts or relative.startswith(("/", "\\")):
                raise AccessDenied("path_escape")
            if "\x00" in relative:
                raise AccessDenied("invalid_path")
            target = (self.workspace_root / relative).resolve(strict=False)
            if not target.is_relative_to(self.scope):
                raise AccessDenied("path_outside_scope")
        if name == "search_text" and not isinstance(args.get("query"), str):
            raise AccessDenied("invalid_query")


def redact(value: str) -> str:
    """No secret values in logs, including errors. Do not log raw arguments."""
    return "<redacted>" if value else ""
