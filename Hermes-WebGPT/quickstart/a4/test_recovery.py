"""Rollback fixture tests. Never touches the user's real AppData."""
import tempfile
import unittest
from pathlib import Path

from quickstart.a4.recovery import NAMES, Recovery, RecoveryError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.recovery = Recovery(self.path)

    def test_all_managed_items_report_only_existence(self):
        self.assertEqual(set(self.recovery.present()), set(NAMES))
        self.assertTrue(all(not v["current"] and not v["previous"]
                            for v in self.recovery.present().values()))

    def test_atomic_previous_and_restore_keeps_current(self):
        for name in NAMES:
            current = self.path / name
            current.write_bytes(b"encrypted-a")
            self.recovery.backup(name)
            current.write_bytes(b"encrypted-b")
            self.recovery.restore(name)
            self.assertEqual(current.read_bytes(), b"encrypted-a")
            self.assertEqual((self.path / (name + ".rollback-current")).read_bytes(),
                             b"encrypted-b")
            self.assertFalse((self.path / (name + ".previous")).exists())

    def test_invalid_file_name_is_blocked(self):
        for name in ("../profile/.env", "secret.txt", "data/relay.sqlite3"):
            with self.assertRaises(RecoveryError):
                self.recovery.backup(name)
            with self.assertRaises(RecoveryError):
                self.recovery.restore(name)

    def test_empty_restore_is_fail_closed(self):
        with self.assertRaises(RecoveryError):
            self.recovery.restore(NAMES[0])

    def test_second_restore_never_overwrites_rollback_current(self):
        name = NAMES[0]
        (self.path / name).write_bytes(b"before")
        self.recovery.backup(name)
        (self.path / name).write_bytes(b"after")
        self.recovery.restore(name)
        self.recovery.backup(name)
        with self.assertRaises(RecoveryError):
            self.recovery.restore(name)

    def test_symlinked_control_file_rejected(self):
        outside = self.path.parent / ("outside_" + self.path.name)
        outside.mkdir(exist_ok=True)
        self.addCleanup(lambda: outside.rmdir())
        try:
            (self.path / NAMES[0]).symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Windows symlink privileges unavailable")
        with self.assertRaises(RecoveryError):
            self.recovery.backup(NAMES[0])
        (self.path / NAMES[0]).unlink()


if __name__ == "__main__":
    unittest.main()
