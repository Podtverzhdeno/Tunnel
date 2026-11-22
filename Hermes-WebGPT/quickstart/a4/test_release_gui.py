"""Optional real Windows acceptance for the A4 GUI release update controls."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]


class ReleaseGuiAcceptance(unittest.TestCase):
    @unittest.skipUnless(
        os.name=="nt" and shutil.which("powershell.exe")
        and os.environ.get("HERMES_A4_UPDATE_GUI_E2E")=="1",
        "opt-in interactive WinForms update/rollback; isolated test state only"
    )
    def test_real_owner_confirmed_update_and_rollback(self):
        runner=ROOT/"quickstart/a4/gui_update_acceptance.py"
        proc=subprocess.run([sys.executable,str(runner)],
              cwd=ROOT,capture_output=True,text=True,timeout=100)
        self.assertEqual(proc.returncode,0,proc.stderr[-2400:])
        receipt=json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["user_files_unchanged"])
        self.assertTrue(receipt["real_winforms_buttons"])
        result=receipt["result"]
        self.assertEqual(result["initial"],"v1.0.0")
        self.assertEqual(result["activated"],"v1.1.0")
        self.assertEqual(result["rollback"],"v1.0.0")
        self.assertEqual(result["native_owner_confirmations"],2)
        self.assertTrue(result["rejected_unpinned"])


if __name__=="__main__":
    unittest.main()
