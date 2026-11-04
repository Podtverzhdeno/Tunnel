"""P085 reviewer client tests: refuses attacker pipe before credential send.

All pipe I/O mocked, no real grants or privileged OS process required.
"""
import ctypes
import json
import struct
import unittest
from ctypes import wintypes as w
from unittest.mock import patch

from workspace_mcp.review_pipe_client import (
    PIPE_DATA_RIGHTS, PIPE_NAME, PipeClientError,
    send_signed_review_request,
    _read_exact, _write_exact,
)

SERVICE_SID="S-1-5-21-1000-1000-1000-101"
REQUEST={
    "task_id":"P152",
    "signed_grant":{"payload":{"nonce":"a"*32},"mac":"b"*64},
    "signed_qa_receipt":{"payload":{"checks":[]},"mac":"c"*64},
}


class FakeKernel:
    def __init__(self,response=None):
        packet=json.dumps(response or {"ok":True,"result":{"state":"RESERVED"}}).encode("ascii")
        self.data=bytearray(struct.pack("!I",len(packet))+packet)
        self.sent=[]
        self.closed=[]
        self.access_requests=[]
    def WaitNamedPipeW(self,name,millis):
        return True
    def CreateFileW(self,*args):
        self.access_requests.append(args)
        return 777
    def WriteFile(self,handle,buf,count,ptr,_):
        self.sent.append(ctypes.string_at(buf,count))
        ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=count
        return True
    def ReadFile(self,handle,buf,count,ptr,_):
        if not self.data:
            return False
        take=min(count,len(self.data),3)
        ctypes.memmove(buf,bytes(self.data[:take]),take)
        del self.data[:take]
        ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=take
        return True
    def CloseHandle(self,handle):
        self.closed.append(handle)
        return True


class ReviewerClientTests(unittest.TestCase):
    def invoke(self, fake, service_sid=SERVICE_SID):
        with patch("workspace_mcp.review_pipe_client._kernel",return_value=fake),patch(
                "workspace_mcp.review_pipe_client._server_sid",
                return_value=service_sid):
            return send_signed_review_request(**REQUEST,
                expected_service_sid=SERVICE_SID)

    def test_framed_request_reply_and_ack(self):
        fake=FakeKernel()
        response=self.invoke(fake)
        self.assertTrue(response["ok"])
        self.assertEqual(fake.closed,[777])
        self.assertEqual(len(fake.sent),2)
        length=struct.unpack("!I",fake.sent[0][:4])[0]
        self.assertEqual(length,len(fake.sent[0])-4)
        self.assertEqual(json.loads(fake.sent[0][4:])["task_id"],"P152")
        self.assertEqual(fake.sent[1],b"\x06")
        self.assertEqual(fake.access_requests[0][1],PIPE_DATA_RIGHTS)

    def test_attacker_server_rejected_before_transmitting_grants(self):
        fake=FakeKernel()
        with self.assertRaisesRegex(PipeClientError,"UNTRUSTED_PIPE_SERVER"):
            self.invoke(fake,service_sid="S-1-5-21-1000-1000-1000-888")
        self.assertEqual(fake.sent,[])
        self.assertEqual(fake.closed,[777])

    def test_invalid_role_and_sid_rejected_before_connect(self):
        for bad in ("architect-reviewer","", "S-1-x"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(PipeClientError,
                                            "INVALID_TRUSTED_CLIENT_CONFIG"):
                    send_signed_review_request(**REQUEST,expected_service_sid=bad)
        with self.assertRaisesRegex(PipeClientError,"INVALID_REVIEW_REQUEST"):
            send_signed_review_request("P152/../",REQUEST["signed_grant"],
                    REQUEST["signed_qa_receipt"],expected_service_sid=SERVICE_SID)

    def test_timeout_and_boolean_invalid(self):
        for timeout in (True,0,31_000):
            with self.subTest(timeout=timeout),self.assertRaisesRegex(
                    PipeClientError,"INVALID_TRUSTED_CLIENT_CONFIG"):
                send_signed_review_request(**REQUEST,
                    expected_service_sid=SERVICE_SID,timeout_ms=timeout)

    def test_partial_reads_are_reassembled(self):
        fake=FakeKernel()
        self.assertTrue(self.invoke(fake)["ok"])
        self.assertEqual(fake.data,bytearray())

    def test_bounded_request_rejects_oversized_credentials(self):
        enlarged={**REQUEST,"signed_grant":{"payload":"x"*9000}}
        with self.assertRaisesRegex(PipeClientError,"REQUEST_TOO_LARGE"):
            send_signed_review_request(**enlarged,expected_service_sid=SERVICE_SID)

    def test_pipe_closed_if_reply_is_truncated(self):
        fake=FakeKernel()
        fake.data=bytearray(struct.pack("!I",40)+b"short")
        with self.assertRaisesRegex(PipeClientError,"PIPE_READ_FAILED"):
            self.invoke(fake)
        self.assertEqual(fake.closed,[777])
        self.assertEqual(len(fake.sent),1)

if __name__=="__main__":
    unittest.main()
