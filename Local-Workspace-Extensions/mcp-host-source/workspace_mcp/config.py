"""Configuration loading and explicit workspace grants."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config.json'
DEFAULT_STATE = Path(__file__).resolve().parent.parent / '.local-mcp-state'
NAME = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,39}$')

class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class Root:
    name: str
    path: Path
    read: bool
    write: bool
    execute: bool
    git: bool


class Settings:
    def __init__(self, config_file=None, state_dir=None):
        self.config_file = Path(config_file or os.environ.get('LOCAL_MCP_CONFIG') or DEFAULT_CONFIG).expanduser().resolve()
        self.state_dir = Path(state_dir or os.environ.get('LOCAL_MCP_STATE') or DEFAULT_STATE).expanduser().resolve()
        if not self.config_file.is_file():
            raise WorkspaceError(f'Config not found: {self.config_file}. Run: python manage.py init')
        try:
            data = json.loads(self.config_file.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise WorkspaceError(f'Invalid config: {exc}') from exc
        if not isinstance(data, dict):
            raise WorkspaceError('Config must be a JSON object')
        self.roots = {}
        for item in data.get('roots', []):
            name = item.get('name', '')
            if not NAME.fullmatch(name) or name in self.roots:
                raise WorkspaceError(f'Invalid/duplicate root name: {name!r}')
            path = Path(item['path']).expanduser().resolve(strict=True)
            if not path.is_dir():
                raise WorkspaceError(f'Root is not a directory: {path}')
            if path == Path(path.anchor) or path == Path.home().resolve():
                raise WorkspaceError('Refusing filesystem root or entire home directory')
            if any(path == other.path for other in self.roots.values()):
                raise WorkspaceError('Duplicate root path')
            self.roots[name] = Root(name, path, bool(item.get('read', True)), bool(item.get('write', False)), bool(item.get('execute', False)), bool(item.get('git', False)))
        if not self.roots:
            raise WorkspaceError('No roots configured. Run: python manage.py add-root ...')
        limits = data.get('limits', {})
        def limit(name, default, minimum, maximum):
            value = limits.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise WorkspaceError(f'Invalid limit {name} (expected {minimum}..{maximum})')
            return value
        self.max_read = limit('max_read_bytes', 262144, 1024, 2097152)
        self.max_write = limit('max_write_bytes', 1048576, 1024, 4194304)
        self.max_output = limit('max_output_bytes', 131072, 1024, 524288)
        self.max_search_files = limit('max_search_files', 2000, 50, 20000)
        self.max_jobs = limit('max_running_jobs', 3, 1, 8)
        self.max_job_seconds = limit('max_job_seconds', 600, 10, 3600)
        self.max_job_log = limit('max_job_log_bytes', 16777216, 1048576, 67108864)
        sec = data.get('security', {})
        self.allow_git_remote = sec.get('allow_git_remote') is True
        self.allowed_commands = sec.get('allowed_commands', [
            'python', 'py', 'pytest', 'git', 'node', 'npm', 'pnpm', 'yarn',
            'mvn', 'gradle', 'ruff', 'mypy', 'cargo', 'go', 'dotnet', 'java', 'tsc'
        ])
        if not isinstance(self.allowed_commands, list) or any(not isinstance(c, str) or not NAME.fullmatch(c) for c in self.allowed_commands):
            raise WorkspaceError('security.allowed_commands must be a list of executable names')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            self.state_dir.chmod(0o700)

    def root(self, name, permission='read'):
        root = self.roots.get(name)
        if root is None:
            raise WorkspaceError(f'Unknown workspace: {name!r}. Call list_workspaces')
        if not getattr(root, permission):
            raise WorkspaceError(f'Workspace {name!r} does not permit {permission}')
        return root


DEFAULT_DATA = {
    'roots': [],
    'limits': {'max_read_bytes': 262144, 'max_write_bytes': 1048576, 'max_output_bytes': 131072,
               'max_search_files': 2000, 'max_running_jobs': 3, 'max_job_seconds': 600, 'max_job_log_bytes': 16777216},
    'security': {'allow_git_remote': False, 'allowed_commands': ['python', 'py', 'pytest', 'git', 'node', 'npm', 'pnpm', 'yarn', 'mvn', 'gradle', 'ruff', 'mypy', 'cargo', 'go', 'dotnet', 'java', 'tsc']}
}
