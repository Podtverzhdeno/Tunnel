"""Run the explicitly opt-in A4 Windows release GUI acceptance."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import unittest

if __name__=="__main__":
    project=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(project))
    os.environ["HERMES_A4_UPDATE_GUI_E2E"]="1"
    suite=unittest.defaultTestLoader.loadTestsFromName(
      "quickstart.a4.test_release_gui.ReleaseGuiAcceptance.test_real_owner_confirmed_update_and_rollback")
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun==1 else 1)
