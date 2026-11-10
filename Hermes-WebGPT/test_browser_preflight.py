from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import browser_preflight as doctor


class BrowserPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tool=SimpleNamespace(__file__=str(Path.cwd()/'tools/browser_tool.py'),
                                  _find_agent_browser=lambda: '/fixture/agent-browser')

    def test_dependency_availability_does_not_claim_navigation(self):
        with patch.object(doctor,'select_native_browser',return_value=(Path('/fixture/native.exe'),'native_win32_x64')), \
             patch.object(doctor,'find_existing_browser',return_value=(Path('/fixture/edge.exe'),'existing_edge')):
            row=doctor.observe_browser(self.tool,{},system='nt',machine='AMD64')
        self.assertEqual(row['status'],'dependencies_available')
        self.assertFalse(row['navigation_verified'])
        self.assertFalse(row['telegram_delivery_verified'])
        self.assertNotIn('/fixture',str(row))

    def test_unavailable_browser_requires_attention(self):
        with patch.object(doctor,'select_native_browser',return_value=(Path('/fixture/native.exe'),'native_win32_x64')), \
             patch.object(doctor,'find_existing_browser',return_value=(None,'automatic_browser_discovery')):
            self.assertEqual(doctor.observe_browser(self.tool,{},system='nt')['status'],'needs_attention')

    def test_source_failure_does_not_expose_exception_text(self):
        self.tool._find_agent_browser=lambda: (_ for _ in ()).throw(OSError('PRIVATE_PATH_AND_SECRET'))
        row=doctor.observe_browser(self.tool,{},system='nt')
        self.assertEqual(row['status'],'observation_unavailable')
        self.assertNotIn('PRIVATE',str(row))

    def test_other_platform_does_not_probe_or_change_environment(self):
        self.tool._find_agent_browser=lambda: self.fail('should not probe')
        env={'AGENT_BROWSER_EXECUTABLE_PATH':'user choice'}
        row=doctor.observe_browser(self.tool,env,system='posix')
        self.assertEqual(row['status'],'not_applicable')
        self.assertEqual(env,{'AGENT_BROWSER_EXECUTABLE_PATH':'user choice'})

    def test_invalid_explicit_executable_is_not_hidden_by_fallback(self):
        with patch.object(doctor,'select_native_browser',return_value=(Path('/fixture/native.exe'),'native_win32_x64')), \
             patch.object(doctor,'find_existing_browser',return_value=(None,'preserved_user_setting')), \
             patch.object(doctor,'_is_x64_pe',return_value=False):
            row=doctor.observe_browser(self.tool,{'AGENT_BROWSER_EXECUTABLE_PATH':'configured missing path'},system='nt')
        self.assertEqual(row['status'],'needs_attention')
        self.assertEqual(row['browser_selection'],'preserved_user_setting')


if __name__=='__main__':unittest.main()
