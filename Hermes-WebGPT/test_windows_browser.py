from pathlib import Path
import json
import struct
import tempfile
import unittest
from types import SimpleNamespace
import hermes_windows_browser as w


class NativeBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='Hermes browser with spaces ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.shim = self.root/'node_modules/.bin/agent-browser'
        self.shim.parent.mkdir(parents=True)
        self.shim.write_text('#!/bin/sh\nnot a Windows executable')
        self.package = self.root/'node_modules/agent-browser'
        (self.package/'bin').mkdir(parents=True)
        self.meta = self.package/'package.json'
        self.meta.write_text(json.dumps({'name':'agent-browser','version':'0.26.0'}))
        self.native = self.package/'bin/agent-browser-win32-x64.exe'
        raw = bytearray(256)
        raw[:2] = b'MZ'
        raw[60:64] = struct.pack('<I',128)
        raw[128:134] = b'PE\0\0\x64\x86'
        self.native.write_bytes(raw)

    def select(self, chosen=None, **kwargs):
        return w.select_native_browser(self.root, chosen or self.shim,
            system=kwargs.get('system','nt'),machine=kwargs.get('machine','AMD64'))

    def test_selects_native_as_one_path_with_spaces(self):
        path, reason = self.select()
        self.assertEqual(path,self.native)
        self.assertEqual(reason,'native_win32_x64')
        self.assertIn(' ',str(path))

    def test_cmd_and_powershell_shims_use_same_native(self):
        for name in ('agent-browser.cmd','agent-browser.ps1'):
            chosen=self.shim.with_name(name);chosen.write_text('synthetic wrapper')
            self.assertEqual(self.select(chosen)[0],self.native)

    def test_linux_is_unchanged(self):
        self.assertEqual(self.select(system='posix')[1],'platform_unchanged')

    def test_other_architecture_is_unchanged(self):
        self.assertIsNone(self.select(machine='ARM64')[0])

    def test_already_native_selection_is_unchanged(self):
        self.assertEqual(self.select(self.native),(None,'launcher_unchanged'))

    def test_external_custom_command_is_unchanged(self):
        path=self.root/'custom/agent-browser.cmd'
        self.assertIsNone(self.select(path)[0])

    def test_npx_fallback_is_not_executed_or_rewritten(self):
        self.assertIsNone(self.select('npx agent-browser')[0])

    def test_missing_native_never_selects_other_platform_binary(self):
        self.native.unlink()
        (self.package/'bin/agent-browser-linux-x64').write_bytes(b'ELF')
        self.assertEqual(self.select(),(None,'native_launcher_unverified'))

    def test_mz_prefix_alone_does_not_qualify(self):
        self.native.write_bytes(b'MZ synthetic non-executable')
        self.assertIsNone(self.select()[0])

    def test_wrong_pe_architecture_does_not_qualify(self):
        raw=bytearray(self.native.read_bytes());raw[132:134]=b'\x4c\x01'
        self.native.write_bytes(raw)
        self.assertIsNone(self.select()[0])

    def test_invalid_pe_offset_does_not_qualify(self):
        raw=bytearray(self.native.read_bytes());raw[60:64]=struct.pack('<I',2**32-1)
        self.native.write_bytes(raw)
        self.assertIsNone(self.select()[0])

    def test_package_identity_must_match(self):
        self.meta.write_text('{"name":"different-package"}')
        self.assertIsNone(self.select()[0])

    def test_malformed_package_does_not_crash(self):
        for text in ('invalid','[]','null'):
            self.meta.write_text(text)
            self.assertIsNone(self.select()[0])

    def test_path_with_shell_metacharacters_remains_literal(self):
        # The resolver never composes a command string or executes this name.
        custom=self.root/'& do-not-execute $(anything)'/'agent-browser'
        self.assertIsNone(self.select(custom)[0])

    def test_existing_edge_is_selected_without_browser_installation(self):
        edge=self.root/'Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
        edge.parent.mkdir(parents=True);edge.write_bytes(self.native.read_bytes())
        self.assertEqual(w.find_existing_browser({'ProgramFiles(x86)':str(self.root/'Program Files (x86)')}),
                         (edge,'existing_edge'))

    def test_explicit_executable_is_preserved_even_when_unavailable(self):
        value={'AGENT_BROWSER_EXECUTABLE_PATH':'C:/custom/browser.exe'}
        self.assertEqual(w.find_existing_browser(value),(None,'preserved_user_setting'))
        self.assertEqual(value['AGENT_BROWSER_EXECUTABLE_PATH'],'C:/custom/browser.exe')

    def test_no_browser_uses_original_automatic_discovery(self):
        self.assertEqual(w.find_existing_browser({}),(None,'automatic_browser_discovery'))

    def test_non_pe_file_is_not_selected_as_browser(self):
        chrome=self.root/'Google/Chrome/Application/chrome.exe'
        chrome.parent.mkdir(parents=True);chrome.write_text('not an executable')
        self.assertIsNone(w.find_existing_browser({'ProgramFiles':str(self.root)})[0])

    def test_relative_program_files_is_not_a_browser_authority(self):
        self.assertIsNone(w.find_existing_browser({'ProgramFiles':'relative-folder'})[0])

    @unittest.skipUnless(w.os.name=='nt','actual Windows configuration mutation')
    def test_configuration_uses_existing_hermes_resolver_cache(self):
        module=SimpleNamespace(__file__=str(self.root/'tools/browser_tool.py'),
            _find_agent_browser=lambda: str(self.shim),_cached_agent_browser=str(self.shim),
            _agent_browser_resolved=True)
        out=w.configure_browser_launcher(module)
        self.assertTrue(out['applied'])
        self.assertEqual(module._cached_agent_browser,str(self.native))


if __name__=='__main__':unittest.main()
