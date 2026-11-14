"""Negative checks for archive file-type metadata before A4 runtime staging.

Only synthetic contents; no live process, profile, connector or credentials.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from quickstart.a4.dependency_closure import REQUIRED_PY_FILES
from quickstart.a4.runtime_bundle import BundleError, build_local_candidate, verify_candidate
from quickstart.a4.runtime_stage import RuntimeStager, StageError


class ZipMetadataSecurityTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.source = self.root / "source"
        self.state = self.root / "state"
        self.source.mkdir()
        self.state.mkdir()
        for name in REQUIRED_PY_FILES:
            (self.source / name).write_text("VALUE = 1\n", encoding="utf8")
        self.valid_zip = self.root / "valid.zip"
        self.pins = build_local_candidate(
            self.source, self.valid_zip, "v1.0.0", explicit_local_only=True
        )

    def _alter_type(self, file_type: int, *, dos_directory: bool = False) -> Path:
        bad_zip = self.root / f"bad-{file_type}-{int(dos_directory)}.zip"
        with zipfile.ZipFile(self.valid_zip) as src, zipfile.ZipFile(
            bad_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as dest:
            for name in src.namelist():
                entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 0 if dos_directory else 3
                entry.external_attr = (
                    (file_type | 0o777) if name == "relay.py" else (stat.S_IFREG | 0o644)
                ) << 16
                if dos_directory and name == "relay.py":
                    entry.external_attr |= 0x10  # DOS directory bit, no trailing slash
                dest.writestr(entry, src.read(name))
        return bad_zip

    def test_unix_symlink_entry_denied_even_with_matching_file_hashes(self):
        bad_zip = self._alter_type(stat.S_IFLNK)
        with self.assertRaisesRegex(BundleError, "unsafe_zip"):
            verify_candidate(bad_zip, self.pins["manifest_sha256"])

    def test_unix_fifo_entry_denied(self):
        bad_zip = self._alter_type(stat.S_IFIFO)
        with self.assertRaisesRegex(BundleError, "unsafe_zip"):
            verify_candidate(bad_zip, self.pins["manifest_sha256"])

    def test_dos_directory_flag_denied_with_regular_file_name(self):
        bad_zip = self._alter_type(stat.S_IFREG, dos_directory=True)
        with self.assertRaisesRegex(BundleError, "unsafe_zip"):
            verify_candidate(bad_zip, self.pins["manifest_sha256"])

    def test_stager_rejects_signed_off_archive_with_symlink_metadata(self):
        bad_zip = self._alter_type(stat.S_IFLNK)
        archive_pin = hashlib.sha256(bad_zip.read_bytes()).hexdigest()
        stager = RuntimeStager(self.state)
        with self.assertRaisesRegex(StageError, "validation_failed"):
            stager.stage(bad_zip, "v1.0.0", self.pins["manifest_sha256"], archive_pin)
        self.assertFalse((self.state / "a4-runtime-versions/v1.0.0").exists())
        self.assertFalse((self.state / "a4-runtime-stage.lock").exists())


if __name__ == "__main__":
    unittest.main()
