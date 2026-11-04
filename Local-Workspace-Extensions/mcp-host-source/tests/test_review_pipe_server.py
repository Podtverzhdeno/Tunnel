"""P085 named-pipe transport: private SDDL and bounded frame negatives.

No production pipe is launched. Security descriptor is parsed by real
Windows Advapi32 when available; all wire fixtures are disposable/fake.
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import unittest
from ctypes import wintypes as w

from workspace_mcp.review_grant_broker import BrokerPolicy
from workspace_mcp.review_pipe_server import (
    MAX_FRAME, PIPE_NAME, PipeProtocolError, _create_pipe, _read_message,
    _write_message, _os_api, pipe_sddl,
)

POLICY = BrokerPolicy(
    service_sid="S-1-5-21-1000-1000-1000-101",
    reviewer_client_sid="S-1-5-21-1000-1000-1000-102",
    untrusted_mcp_sid="S-1-5-21-1000-1000-1000-103",
    reviewer_actor="reviewer:authenticated",
    workspace="projects",
    owner_principals={f"developer-{i}":f"developer:principal-{i}" for i in range(1,5)},
)


class ReadKernel:
    def __init__(self, data: bytes, chunk=7):
        self.data=bytearray(data)
        self.chunk=chunk
    def ReadFile(self, pipe, buf, size, written, overlapped):
        if not self.data:
            ctypes.set_last_error(109)  # ERROR_BROKEN_PIPE, not NO_DATA
            return False
        n=min(size,self.chunk,len(self.data))
        ctypes.memmove(buf,bytes(self.data[:n]),n)
        del self.data[:n]
        ctypes.cast(written, ctypes.POINTER(w.DWORD)).contents.value=n
        return True


class WriteKernel:
    def __init__(self): self.packets=[]
    def WriteFile(self, pipe, buf, size, written, overlapped):
        self.packets.append(ctypes.string_at(buf,size))
        ctypes.cast(written, ctypes.POINTER(w.DWORD)).contents.value=size
        return True


class PipeServerTests(unittest.TestCase):
    def test_broker_transport_refuses_unsigned_qa_configuration(self):
        from unittest.mock import Mock, patch
        from workspace_mcp.review_pipe_server import serve_one
        from workspace_mcp.review_grant_broker import ReviewGrantBroker
        with patch("workspace_mcp.review_pipe_server._create_pipe") as create:
            broker=Mock(spec=ReviewGrantBroker)
            with self.assertRaisesRegex(PipeProtocolError,
                                        "INDEPENDENT_QA_GATE_REQUIRED"):
                serve_one(POLICY,broker)
            create.assert_not_called()

    def test_allowed_client_and_service_only_in_acl(self):
        sddl=pipe_sddl(POLICY)
        self.assertIn(POLICY.service_sid,sddl)
        self.assertIn(POLICY.reviewer_client_sid,sddl)
        self.assertNotIn(POLICY.untrusted_mcp_sid,sddl)
        self.assertIn("0x00120003",sddl)
        self.assertNotIn(";;;WD",sddl)
        self.assertNotIn(";;;AU",sddl)
        self.assertIn("D:P",sddl)

    @unittest.skipUnless(os.name=="nt","Windows only")
    def test_real_advapi_accepts_dacl_security_descriptor(self):
        kernel,advapi=_os_api()
        sec=ctypes.c_void_p()
        self.assertTrue(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            pipe_sddl(POLICY),1,ctypes.byref(sec),None))
        self.assertTrue(sec.value)
        kernel.LocalFree(sec)

    def test_no_production_pipe_with_custom_name(self):
        with self.assertRaisesRegex(PipeProtocolError,"PIPE_NAME_NOT_ALLOWLISTED"):
            _create_pipe(POLICY,name=PIPE_NAME+"-custom")

    def test_partial_read_frame_yields_exact_json(self):
        payload=json.dumps({"task_id":"P152","signed_grant":{}}).encode("ascii")
        kernel=ReadKernel(struct.pack("!I",len(payload))+payload,chunk=2)
        result=_read_message(kernel,123)
        self.assertEqual(result["task_id"],"P152")
        self.assertFalse(kernel.data)

    def test_empty_or_oversized_frame_denied_before_payload(self):
        for n in (0,1,MAX_FRAME+1,2**32-1):
            with self.subTest(n=n),self.assertRaisesRegex(
                    PipeProtocolError,"INVALID_FRAME_LENGTH"):
                _read_message(ReadKernel(struct.pack("!I",n)),123)

    def test_invalid_json_and_list_payload_denied(self):
        for raw,reason in ((b"{bad","INVALID_JSON_FRAME"),
                           (b"[]","INVALID_BROKER_REQUEST")):
            packet=struct.pack("!I",len(raw))+raw
            with self.assertRaisesRegex(PipeProtocolError,reason):
                _read_message(ReadKernel(packet),123)

    def test_stalled_client_cannot_block_service_indefinitely(self):
        from unittest.mock import patch
        class NoDataKernel:
            def ReadFile(self, *args):
                ctypes.set_last_error(232)
                return False
        with patch("workspace_mcp.review_pipe_server.READ_TIMEOUT_SECONDS", 0.025):
            with self.assertRaisesRegex(PipeProtocolError,"PIPE_READ_TIMEOUT"):
                _read_message(NoDataKernel(),123)

    def test_truncated_frame_is_fail_closed(self):
        raw=b'{"task_id":"P152"}'
        data=struct.pack("!I",len(raw))+raw[:5]
        with self.assertRaisesRegex(PipeProtocolError,"PIPE_READ_FAILED"):
            _read_message(ReadKernel(data),123)

    def test_write_frame_is_length_delimited(self):
        kernel=WriteKernel()
        _write_message(kernel,123,{"ok":False,"error_code":"REVIEW_REQUEST_DENIED"})
        payload=kernel.packets[0]
        self.assertEqual(struct.unpack("!I",payload[:4])[0],len(payload)-4)
        self.assertNotIn(b"lease_token",payload)

    def test_write_oversize_response_denied(self):
        with self.assertRaisesRegex(PipeProtocolError,"RESPONSE_TOO_LARGE"):
            _write_message(WriteKernel(),123,{"data":"x"*MAX_FRAME})

if __name__=="__main__":
    unittest.main()
