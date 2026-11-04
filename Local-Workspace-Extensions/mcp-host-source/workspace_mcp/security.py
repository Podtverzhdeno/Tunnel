"""Workspace path guard (best-effort against symlinks; not an OS sandbox)."""
import fnmatch
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from .config import WorkspaceError

DENY_DIRS = {'.git', '.ssh', '.aws', '.azure', '.gnupg', 'node_modules', '__pycache__', '.venv', 'venv', '.local-mcp-state'}
DENY_NAMES = {'id_rsa', 'id_ed25519', 'credentials', 'credentials.json', 'secrets.json', '.npmrc', '.pypirc', '.netrc'}
DENY_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.kdbx'}


def sensitive(parts):
    for index, part in enumerate(parts):
        p = part.casefold()
        if p in DENY_DIRS or p in DENY_NAMES or p.endswith(tuple(DENY_SUFFIXES)):
            return True
        if p == '.env' or (p.startswith('.env.') and p not in ('.env.example', '.env.sample', '.env.template')):
            return True
        if p.startswith('id_rsa.') or p.startswith('id_ed25519.'):
            return True
    return False


def relative_path(raw, allow_root=False):
    if not isinstance(raw, str) or '\x00' in raw or len(raw) > 4096:
        raise WorkspaceError('Invalid path')
    if raw in ('', '.'):
        if allow_root:
            return Path('.')
        raise WorkspaceError('A file or subdirectory path is required')
    windows = PureWindowsPath(raw)
    unix = PurePosixPath(raw)
    if windows.is_absolute() or windows.drive or unix.is_absolute():
        raise WorkspaceError('Absolute or drive-qualified paths are not allowed; use workspace + relative path')
    # Treat both separator styles as separators on every platform.
    tokens = raw.replace('\\', '/').split('/')
    if any(p in ('', '..') for p in tokens):
        raise WorkspaceError('Parent traversal or empty path component is not allowed')
    parts = [p for p in tokens if p != '.']
    if not parts:
        if allow_root:
            return Path('.')
        raise WorkspaceError('Root operations are not allowed')
    if sensitive(parts):
        raise WorkspaceError('Sensitive or generated path is blocked')
    return Path(*parts)


def safe_path(settings, workspace, raw, permission='read', allow_root=False, must_exist=False):
    root = settings.root(workspace, permission)
    rel = relative_path(raw, allow_root=allow_root)
    candidate = root.path / rel
    try:
        resolved = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f'Path cannot be resolved: {exc}') from exc
    if not resolved.is_relative_to(root.path):
        raise WorkspaceError('Path leaves allowed workspace (including symbolic links/junctions)')
    # A benign-looking symlink must not provide a second name for a denied file.
    if sensitive(resolved.relative_to(root.path).parts):
        raise WorkspaceError('Resolved target is a blocked sensitive path')
    for hidden in (settings.config_file, settings.state_dir):
        if resolved == hidden or resolved.is_relative_to(hidden):
            raise WorkspaceError('Internal configuration or state directory is protected')
    # Also protect the literal path when it resolves elsewhere (e.g. symlink itself).
    if (candidate == settings.config_file or candidate.is_relative_to(settings.state_dir)):
        raise WorkspaceError('Internal path is protected')
    return root, candidate, rel.as_posix()


def require_regular(path):
    if not path.is_file():
        raise WorkspaceError('Not a regular file')


def validate_glob(pattern):
    if not isinstance(pattern, str) or len(pattern) > 150 or '..' in pattern or pattern.startswith('/') or '\\' in pattern:
        raise WorkspaceError('Invalid glob')
    return pattern


def display_safe(path, root):
    return path.relative_to(root.path).as_posix()
