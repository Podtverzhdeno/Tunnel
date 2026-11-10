"""Read-only Windows browser dependency report; never launches or navigates."""
from pathlib import Path
import json
import os

from hermes_windows_browser import _is_x64_pe, find_existing_browser, select_native_browser


def observe_browser(browser_module, environ, *, system=None, machine=None):
    system = os.name if system is None else system
    if system != 'nt':
        return {'scope': 'windows_browser_dependencies', 'status': 'not_applicable',
                'navigation_verified': False, 'telegram_delivery_verified': False}
    try:
        chosen = browser_module._find_agent_browser()
        source = Path(browser_module.__file__).resolve().parents[1]
        native, _ = select_native_browser(source, chosen, system=system, machine=machine)
        launcher = native is not None or (Path(chosen).suffix.lower() == '.exe' and _is_x64_pe(Path(chosen)))
        browser, label = find_existing_browser(environ)
        existing = browser is not None
        if label == 'preserved_user_setting':
            existing = _is_x64_pe(Path(environ['AGENT_BROWSER_EXECUTABLE_PATH']))
        return {
            'scope': 'windows_browser_dependencies',
            'status': 'dependencies_available' if launcher and existing else 'needs_attention',
            'native_launcher_available': launcher, 'browser_executable_available': existing,
            'browser_selection': label, 'navigation_verified': False,
            'telegram_delivery_verified': False,
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {'scope': 'windows_browser_dependencies', 'status': 'observation_unavailable',
                'navigation_verified': False, 'telegram_delivery_verified': False}


def main():
    try:
        from tools import browser_tool
        row = observe_browser(browser_tool, os.environ)
    except ImportError:
        row = {'scope': 'windows_browser_dependencies', 'status': 'hermes_tools_unavailable',
               'navigation_verified': False, 'telegram_delivery_verified': False}
    print(json.dumps(row, ensure_ascii=True))
    return 0 if row['status'] in ('dependencies_available', 'not_applicable') else 2


if __name__ == '__main__':
    raise SystemExit(main())
