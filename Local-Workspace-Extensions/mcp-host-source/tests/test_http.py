import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from workspace_mcp.config import Settings
from workspace_mcp.tools import ToolRegistry
from workspace_mcp.http_server import make_handler

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        d=Path(self.tmp.name);(d/'work').mkdir()
        config=d/'config.json';config.write_text(json.dumps({'roots':[{'name':'work','path':str(d/'work'),'read':True}]}))
        self.registry=ToolRegistry(Settings(config,d/'state'))
        self.token='Z'*43  # fixture only; no production secret
        self.server=ThreadingHTTPServer(
            ('127.0.0.1',0),make_handler(self.registry,observer_token=self.token))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def rpc(self,msg,headers=None):
        data=json.dumps(msg).encode()
        request=Request(self.url+'/mcp',data=data,headers={
            'Content-Type':'application/json',
            'Accept':'application/json, text/event-stream',
            'Authorization':'Bearer '+self.token,**(headers or {})})
        with urlopen(request,timeout=5) as r:return r.status,json.loads(r.read())

    def test_health_and_initialize(self):
        with urlopen(self.url+'/healthz') as r:
            health=json.load(r)
            self.assertEqual(health['status'],'ok')
            self.assertEqual(health['tools'],6)
        status,data=self.rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
        self.assertEqual(status,200);self.assertEqual(data['result']['protocolVersion'],'2025-11-25')
        status,data=self.rpc({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual({x['name'] for x in data['result']['tools']},
                         {'list_workspaces','list_directory','directory_tree',
                          'file_info','read_file','search_text'})

    def test_bearer_required_for_rpc_even_with_client_supplied_role(self):
        request=Request(self.url+'/mcp',data=json.dumps({
            'jsonrpc':'2.0','id':4,'method':'tools/list',
            'caller_role':'architect-reviewer'}).encode('utf-8'),
            headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as missing:
            urlopen(request,timeout=5)
        self.assertEqual(missing.exception.code,401)
        for candidate in ('wrong','Bearer '+('Y'*43),'bearer '+self.token):
            request=Request(self.url+'/mcp',data=b'{}',
                headers={'Content-Type':'application/json',
                         'Authorization':candidate})
            with self.assertRaises(HTTPError) as denied:
                urlopen(request,timeout=5)
            self.assertEqual(denied.exception.code,401)

    def test_bad_origin_and_host(self):
        with self.assertRaises(HTTPError) as e:
            self.rpc({'jsonrpc':'2.0','id':1,'method':'ping'},headers={'Origin':'https://evil.example'})
        self.assertEqual(e.exception.code,403)

    def test_unknown_tool(self):
        _,data=self.rpc({'jsonrpc':'2.0','id':10,'method':'tools/call','params':{'name':'imaginary','arguments':{}}})
        self.assertEqual(data['error']['code'],-32602)

    def test_http_notification(self):
        req=Request(self.url+'/mcp',data=json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with urlopen(req,timeout=5) as r:
            self.assertEqual(r.status,202)
            self.assertEqual(r.read(),b'')

    def test_error_from_tool(self):
        _,data=self.rpc({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'read_file','arguments':{'workspace':'work','path':'../etc'}}})
        self.assertTrue(data['result']['isError'])

if __name__=='__main__':unittest.main()
