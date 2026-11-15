"""Offline tests for the read-only localhost-only disconnect doctor."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import json
import unittest
from unittest.mock import patch

TARGET=Path(__file__).with_name('local_workspace_disconnect_doctor.py')
spec=importlib.util.spec_from_file_location('local_disconnect_doctor',TARGET)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Response:
    def __init__(self, obj):self.data=json.dumps(obj).encode('utf8')
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,size):return self.data[:size]
class TextResponse:
    def __init__(self,text):self.data=text.encode('utf8')
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,size):return self.data[:size]
class Opener:
    def __init__(self,response):self.response=response;self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request.full_url,timeout))
        if isinstance(self.response,str):return TextResponse(self.response)
        return Response(self.response)

class Tests(unittest.TestCase):
    def test_disconnected_but_mcp_running(self):
        state={'mcp':{'http_result':'responded','ok':True},
               'tunnel_ready':{'http_result':'responded','ready':True},
               'control_plane':{'http_result':'responded','status':'degraded','reason_code':'network_error'}}
        self.assertEqual(mod.classify(state)['diagnosis'],'TUNNEL_CONTROL_PLANE_DEGRADED')
    def test_mcp_not_running(self):
        self.assertEqual(mod.classify({'mcp':{'http_result':'not_reachable'}})['diagnosis'],'LOCAL_MCP_UNAVAILABLE')
    def test_tunnel_not_running(self):
        result=mod.classify({'mcp':{'http_result':'responded'},'tunnel_ready':{'http_result':'not_reachable'}})
        self.assertEqual(result['diagnosis'],'TUNNEL_CLIENT_UNAVAILABLE')
    def test_local_services_not_proof_of_chatgpt_connection(self):
        state={k:{'http_result':'responded'} for k in ('mcp','tunnel_ready','control_plane')}
        r=mod.classify(state)
        self.assertFalse(r['remote_chatgpt_session_attested'])
        self.assertEqual(r['diagnosis'],'LOOPBACK_SERVICES_RESPONDING_REMOTE_SESSION_UNVERIFIED')
    def test_unknown_secret_fields_suppressed(self):
        result=mod.evaluate_payload({'status':'DEGRADED','reason_code':'network_error',
               'token':'TOP_SECRET','authorization':'Bearer abc','url':'https://private.example',
               'consecutive_failures':58,'backoff':True})
        self.assertNotIn('TOP_SECRET',str(result))
        self.assertNotIn('private.example',str(result))
        self.assertEqual(result['consecutive_failures'],58)
    def test_arbitrary_error_status_not_echoed(self):
        self.assertNotIn('status',mod.evaluate_payload({'status':'secret=123'}))
    def test_loopback_and_bounded_read(self):
        opener=Opener({'status':'ready','token':'SECRET'})
        report=mod.probe(8765,'/healthz',opener)
        self.assertEqual(report['status'],'ready')
        self.assertEqual(opener.calls, [('http://127.0.0.1:8765/healthz',2.0)])
    def test_plaintext_readyz_supported_without_leaking_content(self):
        self.assertEqual(mod.probe(8080,'/readyz',Opener('ready\n')),
                         {'http_result':'responded','status':'ready'})
    def test_unknown_plaintext_response_suppressed(self):
        self.assertEqual(mod.probe(8765,'/healthz',Opener('token SECRET')),
                         {'http_result':'not_json'})
    def test_reject_external_host_via_invalid_path(self):
        with self.assertRaises(ValueError):mod.probe(8765,'http://example.com')
    def test_dns_returns_only_boolean(self):
        a=mod.dns_check(lookup=lambda *a,**kw:[('secret-ip',)])
        self.assertEqual(a,{'dns_name_resolved':True,'target':'api.openai.com'})
    def test_dns_failure(self):
        def fail(*a,**kw):raise OSError('private-path')
        self.assertFalse(mod.dns_check(lookup=fail)['dns_name_resolved'])

if __name__=='__main__':unittest.main(verbosity=2)
