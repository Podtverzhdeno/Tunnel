"""Headless guards for A4 WinForms release action identity.

The opt-in native UI suite verifies actual button clicks. These fast checks
also run on every CI build, to guard against position-based selector regressions.
"""
from __future__ import annotations
import unittest
from quickstart.a4.gui_update_acceptance import PS


class ReleaseGuiSelectorContractTests(unittest.TestCase):
    def test_release_actions_are_selected_by_unique_caption(self):
        self.assertIn("function ButtonByName", PS)
        self.assertIn("if($hits.Count -ne 1)", PS)
        self.assertIn("gui_release_action_ambiguous", PS)
        self.assertIn("gui_release_action_missing_handle", PS)
        for caption in ("Проверить пакет", "Активировать", "Откатить"):
            self.assertIn(f"ButtonByName '{caption}'", PS)

    def test_reordering_or_appending_buttons_cannot_shift_release_action(self):
        for offset in (1, 2, 3):
            self.assertNotIn(f"$buttons[$buttons.Count-{offset}]", PS)
        self.assertIn("$_ .Current.Name".replace(" ", ""), PS)


if __name__ == "__main__":
    unittest.main()
