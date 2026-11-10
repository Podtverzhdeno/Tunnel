"""Select the installed native Windows agent-browser without a shell.

Compatibility shim for Hermes versions that discover node_modules/.bin's
POSIX launcher on Windows. Does not alter installed Hermes or browser profiles.
"""
from pathlib import Path
import json
import os
import platform
import struct


def _is_x64_pe(path):
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open('rb') as stream:
            if stream.read(2) != b'MZ':
                return False
            stream.seek(60)
            offset = struct.unpack('<I', stream.read(4))[0]
            if not 64 <= offset <= 65536:
                return False
            stream.seek(offset)
            header = stream.read(6)
        return header == b'PE\0\0\x64\x86'
    except (OSError, struct.error):
        return False


def select_native_browser(source_root, chosen, *, system=None, machine=None):
    system = os.name if system is None else system
    machine = platform.machine() if machine is None else machine
    if system != 'nt' or machine.lower() not in ('amd64', 'x86_64'):
        return None, 'platform_unchanged'
    root = Path(source_root).resolve()
    path = Path(chosen)
    if not path.is_absolute() or path.is_symlink():
        return None, 'launcher_unchanged'
    # Touch only the known repository-local npm shim selected by Hermes.
    if (path.parent.resolve() != root / 'node_modules' / '.bin' or
            path.name.lower() not in ('agent-browser', 'agent-browser.cmd', 'agent-browser.ps1')):
        return None, 'launcher_unchanged'
    package = root / 'node_modules' / 'agent-browser'
    native = package / 'bin' / 'agent-browser-win32-x64.exe'
    try:
        metadata = json.loads((package / 'package.json').read_text(encoding='utf8'))
        if metadata.get('name') != 'agent-browser' or not _is_x64_pe(native):
            return None, 'native_launcher_unverified'
    except (OSError, ValueError, TypeError, AttributeError):
        return None, 'native_launcher_unverified'
    return native, 'native_win32_x64'


def find_existing_browser(environ):
    """Choose an installed browser, preserving an explicit user setting."""
    if environ.get('AGENT_BROWSER_EXECUTABLE_PATH', '').strip():
        return None, 'preserved_user_setting'
    for app, label in (
            ('Google/Chrome/Application/chrome.exe', 'existing_chrome'),
            ('Microsoft/Edge/Application/msedge.exe', 'existing_edge')):
        for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            parent = environ.get(variable)
            if not parent or not Path(parent).is_absolute():
                continue
            candidate = Path(parent) / app
            if _is_x64_pe(candidate):
                return candidate, label
    return None, 'automatic_browser_discovery'


def configure_browser_launcher(browser_module=None):
    if os.name != 'nt':
        return {'applied': False, 'reason': 'platform_unchanged'}
    try:
        if browser_module is None:
            from tools import browser_tool as browser_module
        original = browser_module._find_agent_browser()
        root = Path(browser_module.__file__).resolve().parents[1]
        native, reason = select_native_browser(root, original)
        # Re-applying the bootstrap is safe when its native cache is already set.
        installed_native = root/'node_modules/agent-browser/bin/agent-browser-win32-x64.exe'
        if native is None and Path(original) == installed_native and _is_x64_pe(installed_native):
            native, reason = installed_native, 'native_win32_x64'
        if native is None:
            return {'applied': False, 'reason': reason}
        # The existing command assembly keeps one executable argv item intact.
        # No cmd.exe, PowerShell, shell=True, npx or user argument rewriting.
        browser_module._cached_agent_browser = str(native)
        browser_module._agent_browser_resolved = True
        executable, label = find_existing_browser(os.environ)
        if executable is not None:
            os.environ['AGENT_BROWSER_EXECUTABLE_PATH'] = str(executable)
            browser_module._cached_chromium_installed = None
        return {'applied': True, 'reason': reason, 'browser_executable': label}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'applied': False, 'reason': 'browser_launcher_unavailable'}
