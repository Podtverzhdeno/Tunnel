"""Negative tests for personal observer bearer bootstrap. No internet/API usage."""
from __future__ import annotations
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("observer_auth",HERE/"observer_auth.py")
auth=importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class ObserverAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host=Path(self.temp.name)/"host"
        self.host.mkdir()
        (self.host/"manage.py").write_text("# test-only host stub\n",encoding="utf-8")

    def test_first_init_idempotent_and_no_secret_printed(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            first=auth.main(["init","--host-root",str(self.host)])
            second=auth.main(["init","--host-root",str(self.host)])
            valid=auth.main(["check","--host-root",str(self.host)])
        self.assertEqual((first,second,valid),(0,0,0))
        key=auth._check(self.host)[0]
        self.assertEqual(len(key),64)
        self.assertNotIn(key,output.getvalue())
        self.assertNotIn("Bearer "+key,output.getvalue())

    def test_partial_state_is_not_silently_rotated(self):
        auth.initialize(self.host)
        folder,token_path,header_path=auth._paths(self.host)
        original=token_path.read_text("ascii")
        header_path.unlink()
        with self.assertRaisesRegex(auth.AuthSetupError,"missing_or_linked"):
            auth.initialize(self.host)
        self.assertEqual(token_path.read_text("ascii"),original)

    def test_mismatched_header_rejected(self):
        auth.initialize(self.host)
        _,_,header_path=auth._paths(self.host)
        header_path.write_text("Bearer "+("A"*64),encoding="ascii")
        with self.assertRaisesRegex(auth.AuthSetupError,"header_mismatch"):
            auth._check(self.host)

    def test_plaintext_and_malformed_token_rejected(self):
        auth.initialize(self.host)
        _,token_path,_=auth._paths(self.host)
        token_path.write_text("unsafe-token",encoding="ascii")
        with self.assertRaises(auth.AuthSetupError):
            auth._check(self.host)

    def test_linked_secret_fail_closed(self):
        auth.initialize(self.host)
        _,_,header_path=auth._paths(self.host)
        original=header_path.read_text("ascii")
        target=Path(self.temp.name)/"external"
        target.write_text(original,encoding="ascii")
        header_path.unlink()
        try:
            header_path.symlink_to(target)
        except (OSError,NotImplementedError):
            self.skipTest("Symlinks not supported for this user")
        with self.assertRaisesRegex(auth.AuthSetupError,"missing_or_linked"):
            auth.initialize(self.host)

    def test_invalid_host_and_port(self):
        with self.assertRaisesRegex(auth.AuthSetupError,"manage_missing"):
            auth.initialize(Path(self.temp.name))
        auth.initialize(self.host)
        with self.assertRaisesRegex(auth.AuthSetupError,"port_invalid"):
            auth.start(self.host,80)


if __name__=="__main__":
    unittest.main(verbosity=2)
