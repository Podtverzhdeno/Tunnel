"""Windows peer identity primitives: no application-supplied actor accepted."""
from __future__ import annotations

import os
import unittest

from workspace_mcp.host_pipe_peer import (
    SID, PeerIdentityError, current_process_sid, named_pipe_client_sid,
)

class PeerIdentityTests(unittest.TestCase):
    def test_bad_pipe_handles_are_rejected_before_os_authentication(self):
        for handle in (-1, 0, None, "architect-reviewer", True):
            with self.subTest(handle=handle):
                with self.assertRaises(PeerIdentityError):
                    named_pipe_client_sid(handle)

    @unittest.skipUnless(os.name == "nt", "Windows test only")
    def test_actual_process_windows_token_is_readable(self):
        sid = current_process_sid()
        self.assertIsInstance(sid, str)
        self.assertRegex(sid, SID)
        self.assertEqual(sid, current_process_sid())

    @unittest.skipUnless(os.name == "nt", "Windows test only")
    def test_no_impersonation_without_pipe(self):
        # Not a named-pipe handle; never trust self-declared SID strings.
        with self.assertRaises(PeerIdentityError):
            named_pipe_client_sid(7)

    @unittest.skipUnless(os.name == "nt", "Windows named pipes only")
    def test_actual_named_pipe_client_sid_via_os_impersonation(self):
        import threading
        import uuid
        from multiprocessing.connection import Listener, Client
        address=r"\\.\pipe\p085-peer-test-"+uuid.uuid4().hex
        ready=threading.Event()
        done=threading.Event()
        failures=[]
        def client():
            try:
                with Client(address, family="AF_PIPE", authkey=b"test-only-auth") as c:
                    c.send_bytes(b"review-p085-test")
                    done.wait(6)
            except Exception as exc:
                failures.append(type(exc).__name__)
        with Listener(address, family="AF_PIPE", authkey=b"test-only-auth") as listener:
            t=threading.Thread(target=client,daemon=True)
            t.start()
            with listener.accept() as pipe:
                self.assertEqual(pipe.recv_bytes(), b"review-p085-test")
                sid=named_pipe_client_sid(pipe.fileno())
                self.assertEqual(sid,current_process_sid())
                done.set()
            t.join(timeout=6)
        self.assertFalse(failures,failures)

    def test_sid_pattern_rejects_claimed_roles(self):
        for value in ("architect-reviewer", "S-1-0", "../sid", "", "S-1-5;exec"):
            self.assertIsNone(SID.fullmatch(value))

if __name__ == "__main__":
    unittest.main()
