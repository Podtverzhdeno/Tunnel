"""Zero-dependency MCP Streamable HTTP server (stateless, JSON response mode).

Implements the compatible 2025-11-25 / 2025-06-18 / 2025-03-26
MCP request/response subset needed for initialization, tool discovery and calls.
No SSE/subscriptions, prompts, resources or server-initiated messages.
"""
import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .config import WorkspaceError

PROTOCOLS=('2025-11-25','2025-06-18','2025-03-26')
SERVER_VERSION='1.0.0'
MAX_BODY=6*1024*1024


def dispatch(registry,message):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):
        return {'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,'error':{'code':-32600,'message':'Invalid JSON-RPC request'}}
    if 'id' not in message:
        return None
    ident=message['id'];method=message['method']
    try:
        if method=='initialize':
            params=message.get('params') or {}
            requested=params.get('protocolVersion','2025-11-25') if isinstance(params,dict) else ''
            version=requested if requested in PROTOCOLS else PROTOCOLS[0]
            result={'protocolVersion':version,'capabilities':{'tools':{'listChanged':False}},
                    'serverInfo':{'name':'Local Workspace MCP','version':SERVER_VERSION},
                    'instructions':'Use list_workspaces first. All paths are workspace-relative. Read SHA before overwriting. Start jobs then poll output/status. No shell is exposed.'}
        elif method=='ping':
            result={}
        elif method=='tools/list':
            result={'tools':registry.list()}
        elif method=='tools/call':
            params=message.get('params')
            if not isinstance(params,dict) or not isinstance(params.get('name'),str):
                raise WorkspaceError('tools/call requires params.name')
            name=params['name']
            if not registry.available(name):
                # A tool hidden by the host profile must be indistinguishable
                # from a nonexistent tool; never disclose disabled capabilities.
                return {'jsonrpc':'2.0','id':ident,'error':{'code':-32602,'message':'Unknown tool'}}
            try:
                outcome=registry.call(name,params.get('arguments',{}))
                result={'content':[{'type':'text','text':json.dumps(outcome,ensure_ascii=False,default=str)}],'isError':False}
            except (WorkspaceError,ValueError,OSError) as exc:
                result={'content':[{'type':'text','text':str(exc)[:1500]}],'isError':True}
        else:
            return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}
        return {'jsonrpc':'2.0','id':ident,'result':result}
    except (WorkspaceError,ValueError) as exc:
        return {'jsonrpc':'2.0','id':ident,'error':{'code':-32602,'message':str(exc)}}


class RateLimiter:
    def __init__(self):
        self.lock=threading.Lock();self.timestamps=[]
    def allow(self):
        now=time.monotonic()
        with self.lock:
            self.timestamps=[t for t in self.timestamps if now-t<1]
            if len(self.timestamps)>=20:return False
            self.timestamps.append(now);return True


def make_handler(registry, *, observer_token=None):
    # Process-scoped read-only identity is not a privileged reviewer identity.
    # The token must come from a protected launcher/environment, not from
    # client-controlled MCP arguments, JSON, URL queries or cookies.
    registry.assert_safe_http_profile()
    if (type(observer_token) is not str or not 43 <= len(observer_token) <= 128
        or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'
               for ch in observer_token)):
        raise WorkspaceError('OBSERVER_HTTP_AUTH_TOKEN_REQUIRED')
    limiter=RateLimiter()
    class Handler(BaseHTTPRequestHandler):
        server_version='WorkspaceMCP/1.0'
        sys_version=''

        def _validate_headers(self):
            host=self.headers.get('Host','').lower()
            if host not in ('localhost','127.0.0.1',f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'):
                self._json(403,{'error':'Host is not loopback'});return False
            origin=self.headers.get('Origin')
            if origin and origin.lower() not in (f'http://localhost:{self.server.server_port}',f'http://127.0.0.1:{self.server.server_port}'):
                self._json(403,{'error':'Invalid Origin'});return False
            return True

        def _json(self,status,body):
            data=json.dumps(body,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def do_GET(self):
            if not self._validate_headers():return
            if self.path in ('/healthz','/'):
                self._json(200,{'status':'ok','server':'local-workspace-mcp','mcp_url':f'http://127.0.0.1:{self.server.server_port}/mcp',
                                'tools':len(registry.list()),'workspaces':len(registry.s.roots)})
            elif self.path=='/mcp':
                # Stateless JSON response: no SSE stream exists to establish.
                self.send_response(405);self.send_header('Allow','POST');self.send_header('Content-Length','0');self.end_headers()
            else:self._json(404,{'error':'Not found'})

        def do_POST(self):
            if self.path!='/mcp':
                self._json(404,{'error':'Not found'});return
            if not limiter.allow():
                self._json(429,{'error':'Rate limit exceeded (20 requests/s)'});return
            try:
                length=int(self.headers.get('Content-Length','-1'))
            except ValueError:
                self._json(411,{'error':'Content-Length required'});return
            if length<0 or length>MAX_BODY:
                self._json(413,{'error':'Invalid request size'});return
            if 'application/json' not in self.headers.get('Content-Type',''):
                self._json(415,{'error':'Expected application/json'});return
            # On Windows, writing 401 while request bytes are still unread
            # can tear down the TCP connection before the client receives
            # the HTTP status. Consume only a bounded, timed frame; never
            # parse the untrusted JSON before checking the bearer.
            try:
                self.connection.settimeout(10)
                raw=self.rfile.read(length)
                if len(raw)!=length:
                    self.close_connection=True
                    return
            except (TimeoutError,OSError):
                self.close_connection=True
                return
            # Body is now drained, so 401/403 can be sent without a Windows
            # TCP reset caused by a premature response to an unread POST.
            if not self._validate_headers():return
            supplied=self.headers.get('Authorization','')
            expected='Bearer '+observer_token
            if len(supplied)>len(expected)+4 or not hmac.compare_digest(supplied,expected):
                self._json(401,{'error':'Unauthorized'});return
            try:
                msg=json.loads(raw)
            except (UnicodeError,ValueError):
                self._json(400,{'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}});return
            if isinstance(msg,list):
                self._json(400,{'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Batch messages not supported'}});return
            version=self.headers.get('MCP-Protocol-Version')
            if version and version not in PROTOCOLS and (not isinstance(msg,dict) or msg.get('method')!='initialize'):
                self._json(400,{'error':'Unsupported MCP protocol version','supported':list(PROTOCOLS)});return
            response=dispatch(registry,msg)
            if response is None:
                self.send_response(202);self.send_header('Content-Length','0');self.end_headers()
            else:self._json(200,response)

        def log_message(self,fmt,*args):
            # Do not record tools/call JSON bodies or headers (may carry secrets).
            logging.info('%s %s',self.address_string(),fmt%args)
    return Handler


def serve(registry,port=8765):
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid port')
    import os
    server=ThreadingHTTPServer(('127.0.0.1',port),make_handler(
        registry,observer_token=os.environ.get('LOCAL_MCP_OBSERVER_BEARER')))
    server.daemon_threads=True
    print(f'Local Workspace MCP listening at http://127.0.0.1:{port}/mcp',flush=True)
    try:server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:pass
    finally:
        registry.jobs.shutdown()
        server.server_close()
