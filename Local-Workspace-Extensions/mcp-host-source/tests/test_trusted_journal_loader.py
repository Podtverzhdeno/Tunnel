"""P085 pinned trusted MergeIntent loading uses only protected test folders."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from workspace_mcp.review_grants import GrantDenied
from workspace_mcp.trusted_journal_loader import load_pinned_journal, source_sha


class PinnedJournalTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.folder=self.root/"p085_trusted"
        self.folder.mkdir()
        self.path=self.folder/"review_merge.py"
        self.canonical=Path(__file__).resolve().parents[2]/"review_merge.py"
        self.path.write_bytes(self.canonical.read_bytes())
        self.sha=hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_valid_canonical_copy_imported_only_from_pinned_path(self):
        self.assertEqual(source_sha(self.root),self.sha)
        cls=load_pinned_journal(self.root,self.sha)
        intent=cls(self.root,self.root/".agent-team","P152")
        self.assertFalse(intent.pending())
        self.assertTrue(str(cls.__module__).startswith("_p085_owner_pinned_merge"))

    def test_modified_journal_refused_before_import(self):
        self.path.write_text("print('untrusted code')\n",encoding="utf-8")
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_PIN_MISMATCH"):
            load_pinned_journal(self.root,self.sha)

    def test_wrong_pin_refused(self):
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_PIN_MISMATCH"):
            load_pinned_journal(self.root,"f"*64)

    def test_missing_journal_refused(self):
        self.path.unlink()
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_MISSING"):
            source_sha(self.root)

    def test_linked_journal_refused(self):
        tmp=self.root/"elsewhere.py"
        tmp.write_bytes(self.canonical.read_bytes())
        self.path.unlink()
        try:
            self.path.symlink_to(tmp)
        except (OSError,NotImplementedError):
            self.skipTest("symlink not permitted")
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_LINKED"):
            source_sha(self.root)

    def test_valid_hash_of_wrong_interface_is_not_accepted(self):
        self.path.write_text("class Other: pass\n",encoding="utf-8")
        h=source_sha(self.root)
        with self.assertRaisesRegex(GrantDenied,"TRUSTED_JOURNAL_INTERFACE_INVALID"):
            load_pinned_journal(self.root,h)

if __name__=="__main__":
    unittest.main()
