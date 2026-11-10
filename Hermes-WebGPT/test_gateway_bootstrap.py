from pathlib import Path
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import gateway_bootstrap as bootstrap


class GatewayBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'gateway_bootstrap.py').write_text('synthetic bootstrap')
        (self.root/'hermes_windows_browser.py').write_text('synthetic launcher')
        self.gateway=types.ModuleType('hermes_cli.gateway')
        self.gateway.run_gateway=Mock()

    def invoke(self):
        with patch.object(bootstrap,'__file__',str(self.root/'gateway_bootstrap.py')), \
             patch.object(bootstrap,'configure_browser_launcher',return_value={'applied':True,'reason':'native_win32_x64'}), \
             patch.dict(sys.modules,{'hermes_cli.gateway':self.gateway}):
            bootstrap.run()

    def test_records_runtime_pid_before_starting_same_gateway(self):
        self.invoke()
        row=json.loads((self.root/'gateway_browser_runtime.json').read_text())
        self.assertEqual(row['pid'],bootstrap.os.getpid())
        self.assertTrue(row['browser_launcher']['applied'])
        self.assertEqual(len(row['browser_shim_sha256']),64)
        self.gateway.run_gateway.assert_called_once_with(verbose=0,quiet=False,replace=False)

    def test_failed_receipt_does_not_claim_a_started_gateway(self):
        with patch.object(bootstrap.os,'replace',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):self.invoke()
        self.gateway.run_gateway.assert_not_called()
        self.assertEqual(list(self.root.glob('.gateway-runtime-*')),[])


if __name__=='__main__':unittest.main()
