"""Loopback model adapter for Hermes and the existing Web Companion.

No ChatGPT private APIs, cookies, model billing or Telegram credentials here.
The browser worker supplies a verified visible reply. Tool calls are returned
as data to Hermes; this process never executes them.
"""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import re
import secrets
import select
import socket
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL='web-chatgpt-high'
MODEL_CDP='web-chatgpt-high-cdp'
MAX_BODY=524288
MAX_PROMPT=120000
TERMINAL={'done','blocked','uncertain'}
from hermes_tool_schema_guard import ToolSchemaError, validate_offered_tool_arguments
from model_prompt_compaction import model_request_for_web

# Capture these once at import, not on every health request. A later disk-only
# update must remain distinguishable from the build loaded by this process.
RUNTIME_BUILD={name:hashlib.sha256((Path(__file__).resolve().parent/name).read_bytes()).hexdigest()
               for name in ('relay.py','hermes_tool_schema_guard.py',
                            'model_prompt_compaction.py','model_news_prefetch.py')}
class RelayError(ValueError):pass
def compact(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)

PROMPT_SOFT_LIMIT=MAX_PROMPT-35000  # reserve room for the ChatGPT wrapper, response, and tokenization variation

def _compacted_tool_result(content, retain):
    """Explicit omission marker; never invent a model-written summary."""
    tail=min(400,retain//3)
    head=max(0,retain-tail)
    return compact({'_hermes_relay_compacted':True,
                    'notice':'Older tool output compacted to fit the context. Omitted material is unavailable; call the tool again for specific data when necessary.',
                    'original_characters':len(content),
                    'omitted_characters':len(content)-head-tail,
                    'head':content[:head],
                    'tail':content[-tail:] if tail else ''})

def _fit_tool_results_to_prompt_budget(request):
    """Shrink old tool *contents* before the provider bound; preserve all other messages, schemas and IDs."""
    total=len(compact(request))
    if total<=PROMPT_SOFT_LIMIT:return request
    revised=list(request['messages'])
    candidate=dict(request)
    candidate['messages']=revised
    for i,message in enumerate(request['messages']):
        if not isinstance(message,dict) or message.get('role')!='tool':continue
        content=message.get('content')
        if not isinstance(content,str) or len(content)<1200:continue
        # Work from oldest tool result to newest, retaining as much source text
        # as possible. Never remove an assistant tool call or a tool message.
        def size_with(retain):
            copy=dict(message)
            copy['content']=_compacted_tool_result(content,retain)
            revised[i]=copy
            return len(compact(candidate))
        minimum=min(500,len(content)-1)
        smallest=size_with(minimum)
        if smallest>=total:
            revised[i]=message
            continue
        if smallest>PROMPT_SOFT_LIMIT:
            total=smallest
            continue
        lo,hi=minimum,len(content)-1
        best=minimum
        while lo<=hi:
            mid=(lo+hi)//2
            measured=size_with(mid)
            if measured<=PROMPT_SOFT_LIMIT:
                best=mid
                lo=mid+1
            else:hi=mid-1
        total=size_with(best)
        if total<=PROMPT_SOFT_LIMIT:return candidate
    # If even all tool results cannot reach the soft goal, retain the
    # previous hard cap rather than silently dropping system/user content.
    if total<=MAX_PROMPT-5000:return candidate
    raise RelayError('prompt_too_large')

def chat_url(value):return isinstance(value,str) and re.fullmatch(r'https://chatgpt\.com/c/[a-z0-9-]+',value) is not None

def normalize_request(value):
    if not isinstance(value,dict) or value.get('model') not in (MODEL,MODEL_CDP):raise RelayError('unsupported_model')
    messages=value.get('messages')
    if not isinstance(messages,list) or not messages or len(messages)>400:raise RelayError('invalid_messages')
    for message in messages:
        if not isinstance(message,dict) or message.get('role') not in {'system','developer','user','assistant','tool'}:
            raise RelayError('invalid_message_role')
        content=message.get('content')
        if isinstance(content,list):
            if any(not isinstance(x,dict) or x.get('type')!='text' or not isinstance(x.get('text'),str) for x in content):
                raise RelayError('text_only_adapter')
        elif content is not None and not isinstance(content,str):raise RelayError('invalid_message_content')
    tools=value.get('tools',[])
    if not isinstance(tools,list) or len(tools)>100:raise RelayError('invalid_tools')
    names=[]
    for tool in tools:
        if not isinstance(tool,dict) or tool.get('type')!='function':raise RelayError('function_tools_only')
        fn=tool.get('function',{})
        if not isinstance(fn,dict) or not isinstance(fn.get('name'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',fn['name']):
            raise RelayError('invalid_tool_name')
        names.append(fn['name'])
    if len(names)!=len(set(names)):raise RelayError('duplicate_tool_name')
    choice=value.get('tool_choice','auto' if tools else 'none')
    if (isinstance(choice,str) and choice not in ('auto','none','required')) or not isinstance(choice,(str,dict)):
        raise RelayError('invalid_tool_choice')
    if isinstance(choice,dict):
        if choice.get('type')!='function' or choice.get('function',{}).get('name') not in names:raise RelayError('invalid_tool_choice')
    if choice=='required' and not tools:raise RelayError('tools_required')
    if value.get('n',1)!=1:raise RelayError('one_choice_only')
    normalized={k:value[k] for k in ('model','messages','tools','tool_choice','temperature','max_tokens','max_completion_tokens','response_format') if k in value}
    normalized=_fit_tool_results_to_prompt_budget(normalized)
    if len(compact(normalized))>MAX_PROMPT-5000:raise RelayError('prompt_too_large')
    return normalized

def prompt_for(request,ident):
    return ('You are the model backend for a user-owned Hermes agent. The JSON below contains its conversation and tool schemas. '
            'Follow its system instructions, reason about the next step, but do not run your own browser/MCP/local tools. '
            'To use a Hermes tool, request it as data; Hermes executes it through its own permissions. '
            'Return ONLY one JSON object, optionally in one json code fence, with this exact shape: '
            '{"request_id":"'+ident+'","content":"answer or null when calling tools","tool_calls":[{"name":"exact allowed name","arguments":{}}]}. '
            'Use an empty tool_calls array for a final answer. Do not invent tool results. '
            'The request_id must match exactly. Never output a tool call not offered in this request. '
            'This is one inference request, not an instruction to schedule more chat turns.\n\n'
            +compact({'request_id':ident,'request':model_request_for_web(request)})+'\n\n[HR-'+ident+']')

def parse_reply(text,request,ident):
    if not isinstance(text,str) or not text or len(text)>200000:raise RelayError('invalid_reply')
    value=text.strip()
    if value.startswith('```json\n') and value.endswith('\n```'):value=value[8:-4]
    elif value.startswith('```\n') and value.endswith('\n```'):value=value[4:-4]
    try:obj=json.loads(value)
    except (ValueError,RecursionError) as exc:raise RelayError('reply_not_json') from exc
    if not isinstance(obj,dict) or obj.get('request_id')!=ident:raise RelayError('reply_binding_mismatch')
    content=obj.get('content');calls=obj.get('tool_calls',[])
    if content is not None and not isinstance(content,str):raise RelayError('invalid_reply_content')
    if not isinstance(calls,list) or len(calls)>10:raise RelayError('invalid_reply_tools')
    names={t['function']['name'] for t in request.get('tools',[])}
    choice=request.get('tool_choice','auto' if names else 'none')
    if choice=='none' and calls:raise RelayError('tools_disabled')
    if choice=='required' and not calls:raise RelayError('tool_call_required')
    message={'role':'assistant','content':content}
    if calls:
        parsed=[]
        for i,call in enumerate(calls):
            if not isinstance(call,dict) or call.get('name') not in names or not isinstance(call.get('arguments'),dict):
                raise RelayError('unoffered_or_invalid_tool')
            if isinstance(choice,dict) and call['name']!=choice['function']['name']:raise RelayError('wrong_required_tool')
            try:
                validate_offered_tool_arguments(request.get('tools',[]),call['name'],call['arguments'])
            except ToolSchemaError as exc:
                raise RelayError('invalid_tool_arguments') from exc
            parsed.append({'id':'call_'+ident.replace('-','')+'_'+str(i),'type':'function',
                           'function':{'name':call['name'],'arguments':compact(call['arguments'])}})
        message['tool_calls']=parsed
    elif not content:raise RelayError('empty_reply')
    return {'id':'chatcmpl-hermes-'+ident,'object':'chat.completion','created':int(time.time()),'model':request['model'],
            'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if calls else 'stop'}]}

class Queue:
    def __init__(self,path):
        self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, payload TEXT, state TEXT,
          worker TEXT, lease TEXT, tab INTEGER, saved_url TEXT, result TEXT, reason TEXT, created REAL)''')
        # Additive route ledger: old requests without a row always belong to the
        # existing browser extension. Both workers can coexist without stealing
        # each other's requests. No migration of old request payloads or leases.
        self.db.execute('''CREATE TABLE IF NOT EXISTS transport_routes (
          request_id TEXT PRIMARY KEY,
          transport TEXT NOT NULL CHECK(transport IN ('extension','playwright')))''')
        # Stable turn identities are optional for legacy Hermes clients.
        # When supplied, never replay a turn through another transport.
        self.db.execute('''CREATE TABLE IF NOT EXISTS turn_bindings (
          turn_id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          payload_sha TEXT NOT NULL,
          transport TEXT NOT NULL CHECK(transport IN ('extension','playwright')))''')
        # Playwright pages have their own UUID identity; extension tab numbers
        # must never be substituted for browser page ownership.
        self.db.execute('''CREATE TABLE IF NOT EXISTS page_owners (
          request_id TEXT PRIMARY KEY,
          page_key TEXT NOT NULL UNIQUE,
          worker TEXT NOT NULL)''')
        self.lock=threading.RLock()
    def close(self):self.db.close()
    def read(self,ident):
        with self.lock:
            row=self.db.execute('SELECT * FROM requests WHERE id=?',(ident,)).fetchone()
            if not row:raise RelayError('unknown_request')
            return dict(row)
    def read_status(self,ident):
        """Poll state only; fetch the potentially large payload/result once at completion."""
        with self.lock:
            row=self.db.execute('SELECT state,reason FROM requests WHERE id=?',(ident,)).fetchone()
            if not row:raise RelayError('unknown_request')
            return dict(row)
    def transport_for(self,ident):
        """Authoritative route, including pre-migration extension requests."""
        with self.lock:
            row=self.db.execute('''
                SELECT COALESCE(transport_routes.transport,'extension') AS transport
                FROM requests LEFT JOIN transport_routes
                  ON transport_routes.request_id=requests.id
                WHERE requests.id=?''',(ident,)).fetchone()
            if row is None:raise RelayError('unknown_request')
            return row['transport']
    def status_counts(self):
        """Small fixed-schema health snapshot with no prompts, IDs or secrets."""
        states=('queued','claimed','intent','saved','done','blocked','uncertain')
        counts={transport:{state:0 for state in states}
                for transport in ('extension','playwright')}
        with self.lock:
            rows=self.db.execute('''
                SELECT COALESCE(transport_routes.transport,'extension') AS transport,
                       requests.state AS state, COUNT(*) AS total
                FROM requests LEFT JOIN transport_routes
                  ON transport_routes.request_id=requests.id
                GROUP BY transport,requests.state''').fetchall()
            for row in rows:
                if row['transport'] in counts and row['state'] in counts[row['transport']]:
                    counts[row['transport']][row['state']]=row['total']
        return counts
    def aged_active_counts(self,min_age_seconds=600):
        """Redacted age-since-creation audit; NOT evidence a worker is dead.

        Never mutates requests, exposes IDs/prompts, or frees capacity.
        Creation time is not a worker heartbeat or a lease expiration.
        """
        if type(min_age_seconds) is not int or min_age_seconds<60:
            raise RelayError('invalid_age_threshold')
        states=('claimed','intent','saved')
        counts={transport:{state:0 for state in states}
                for transport in ('extension','playwright')}
        cutoff=time.time()-min_age_seconds
        with self.lock:
            rows=self.db.execute('''
                SELECT COALESCE(transport_routes.transport,'extension') AS transport,
                       requests.state AS state,COUNT(*) AS total
                FROM requests LEFT JOIN transport_routes
                  ON transport_routes.request_id=requests.id
                WHERE requests.state IN ('claimed','intent','saved')
                  AND requests.created<=?
                GROUP BY transport,requests.state''',(cutoff,)).fetchall()
            for row in rows:
                if row['transport'] in counts and row['state'] in counts[row['transport']]:
                    counts[row['transport']][row['state']]=row['total']
        return counts
    def submit(self,value,transport='extension',turn_id=None):
        if transport not in ('extension','playwright'):raise RelayError('invalid_transport')
        if turn_id is not None and (not isinstance(turn_id,str) or
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{11,127}',turn_id) is None):
            raise RelayError('invalid_turn_id')
        request=normalize_request(value)
        # The opt-in model alias may only be assigned to Playwright. Legacy
        # Hermes clients continue using MODEL without any routing changes.
        if request['model']==MODEL_CDP and transport!='playwright':
            raise RelayError('transport_model_mismatch')
        payload=compact(request)
        digest=hashlib.sha256(payload.encode()).hexdigest()
        # Legacy requests keep their exact original fingerprint. A new,
        # explicitly named logical turn gets a distinct binding even if the
        # text matches an earlier intentional message.
        dedupe=('turn:'+turn_id+'|'+payload if turn_id is not None else
                payload if transport=='extension' else 'playwright\\0'+payload)
        fingerprint=hashlib.sha256(dedupe.encode()).hexdigest()
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if turn_id is not None:
                    bound=self.db.execute(
                        'SELECT request_id,payload_sha,transport FROM turn_bindings WHERE turn_id=?',
                        (turn_id,)).fetchone()
                    if bound:
                        if bound['transport']!=transport:
                            raise RelayError('transport_switch_forbidden')
                        if bound['payload_sha']!=digest:
                            raise RelayError('turn_payload_conflict')
                        self.db.execute('COMMIT')
                        return bound['request_id']
                row=self.db.execute('SELECT id FROM requests WHERE fingerprint=?',(fingerprint,)).fetchone()
                if row:
                    if turn_id is not None:raise RelayError('turn_binding_missing')
                    self.db.execute('COMMIT')
                    return row['id']
                pending=self.db.execute("SELECT COUNT(*) FROM requests WHERE state NOT IN ('done','blocked','uncertain')").fetchone()[0]
                if pending>=5:raise RelayError('queue_full')
                ident=str(uuid.uuid4())
                self.db.execute('INSERT INTO requests(id,fingerprint,payload,state,created) VALUES (?,?,?,?,?)',
                                (ident,fingerprint,payload,'queued',time.time()))
                if transport=='playwright':
                    self.db.execute('INSERT INTO transport_routes(request_id,transport) VALUES (?,?)',
                                    (ident,transport))
                if turn_id is not None:
                    self.db.execute('INSERT INTO turn_bindings(turn_id,request_id,payload_sha,transport) VALUES (?,?,?,?)',
                                    (turn_id,ident,digest,transport))
                self.db.execute('COMMIT')
                return ident
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
    def claim(self,worker,transport='extension'):
        if transport not in ('extension','playwright'):raise RelayError('invalid_transport')
        try:uuid.UUID(worker)
        except (ValueError,TypeError,AttributeError) as exc:raise RelayError('invalid_worker') from exc
        with self.lock:
            # Inter-process serialization: checking active ownership before
            # BEGIN IMMEDIATE permitted two different processes to claim at
            # the same time. Keep the initial global serial safety limit.
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if self.db.execute("SELECT 1 FROM requests WHERE state IN ('claimed','intent','saved')").fetchone():
                    self.db.execute('COMMIT')
                    return None
                row=self.db.execute('''
                    SELECT requests.* FROM requests
                    LEFT JOIN transport_routes ON transport_routes.request_id=requests.id
                    WHERE requests.state='queued'
                      AND COALESCE(transport_routes.transport,'extension')=?
                    ORDER BY requests.created LIMIT 1''',(transport,)).fetchone()
                if row is None:
                    self.db.execute('COMMIT')
                    return None
                # Prepare the entire response BEFORE committing ownership.
                # A malformed/unsupported prompt or formatter exception must
                # not leave a permanently claimed request with no worker ACK.
                prompt=prompt_for(json.loads(row['payload']),row['id'])
                lease=secrets.token_urlsafe(32)
                self.db.execute("UPDATE requests SET state='claimed',worker=?,lease=? WHERE id=?",(worker,lease,row['id']))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            return {'id':row['id'],'lease':lease,'prompt':prompt,
                    'transport':transport}
    def event(self,ident,lease,action,tab=None,url='',text='',reason='',page_key=None):
        with self.lock:
            row=self.read(ident)
            if not isinstance(lease,str) or not hmac.compare_digest(row['lease'] or '',lease):
                raise RelayError('lease_mismatch')
            route=self.db.execute('SELECT transport FROM transport_routes WHERE request_id=?',(ident,)).fetchone()
            transport=route['transport'] if route else 'extension'
            if transport=='extension' and page_key is not None:
                raise RelayError('extension_page_key_forbidden')
            if transport=='playwright' and tab is not None:
                raise RelayError('playwright_tab_forbidden')
            if action=='inspect':
                return {'ok':True,'state':row['state'],'tab':row['tab'],
                        'transport':transport}
            if action=='intent':
                if transport=='extension':
                    # A pre-intent handoff can produce another claimed state.
                    # The write must match this event's original lease as well.
                    if row['state']!='claimed' or type(tab) is not int or tab<1:
                        raise RelayError('click_intent_not_available')
                    updated=self.db.execute(
                        "UPDATE requests SET state='intent',tab=? WHERE id=? AND state='claimed' AND lease=?",
                        (tab,ident,lease))
                    if updated.rowcount!=1:raise RelayError('terminal_state_changed')
                    return {'ok':True,'state':'intent'}
                # Page identity is durable and unique across the queue. Bind
                # it atomically with the click intent; never use fake tab IDs.
                if (not isinstance(page_key,str) or
                        not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',page_key)):
                    raise RelayError('invalid_page_key')
                try:
                    parsed=uuid.UUID(page_key)
                except ValueError as exc:
                    raise RelayError('invalid_page_key') from exc
                if parsed.version!=4 or str(parsed)!=page_key:
                    raise RelayError('invalid_page_key')
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    active=self.db.execute(
                        'SELECT state,lease,worker FROM requests WHERE id=?',(ident,)).fetchone()
                    if (active is None or active['state']!='claimed' or
                            not hmac.compare_digest(active['lease'] or '',lease)):
                        raise RelayError('click_intent_not_available')
                    try:
                        self.db.execute(
                            'INSERT INTO page_owners(request_id,page_key,worker) VALUES (?,?,?)',
                            (ident,page_key,active['worker']))
                    except sqlite3.IntegrityError as exc:
                        raise RelayError('page_key_already_owned') from exc
                    self.db.execute("UPDATE requests SET state='intent' WHERE id=?",(ident,))
                    self.db.execute('COMMIT')
                except BaseException:
                    self.db.execute('ROLLBACK')
                    raise
                return {'ok':True,'state':'intent'}
            if transport=='playwright':
                owner=self.db.execute(
                    'SELECT page_key FROM page_owners WHERE request_id=?',(ident,)).fetchone()
                if owner:
                    if not isinstance(page_key,str) or not hmac.compare_digest(owner['page_key'],page_key):
                        raise RelayError('page_mismatch')
                elif page_key is not None:
                    raise RelayError('page_without_intent')
            elif row['tab'] is not None and tab!=row['tab']:
                raise RelayError('tab_mismatch')
            if action=='saved':
                if row['state'] not in ('intent','saved') or not chat_url(url):
                    raise RelayError('saved_message_not_bound')
                if row['saved_url'] and row['saved_url']!=url:
                    raise RelayError('conversation_changed')
                # Conditional SQL write prevents an in-flight pre-cancel
                # observation from overwriting a later terminal decision.
                updated=self.db.execute(
                    "UPDATE requests SET state='saved',saved_url=? "
                    "WHERE id=? AND state=? AND (saved_url IS NULL OR saved_url=?)",
                    (url,ident,row['state'],url))
                if updated.rowcount!=1:raise RelayError('terminal_state_changed')
                return {'ok':True,'state':'saved'}
            if action=='result':
                if row['state']=='done':return {'ok':True,'state':'done'}
                if row['state']!='saved' or row['saved_url']!=url:
                    raise RelayError('result_not_bound')
                try:
                    result=parse_reply(text,json.loads(row['payload']),ident)
                except RelayError as exc:
                    updated=self.db.execute(
                        "UPDATE requests SET state='blocked',reason=? "
                        "WHERE id=? AND state='saved' AND saved_url=?",
                        (str(exc),ident,url))
                    if updated.rowcount!=1:
                        raise RelayError('terminal_state_changed') from exc
                    raise
                updated=self.db.execute(
                    "UPDATE requests SET state='done',result=? "
                    "WHERE id=? AND state='saved' AND saved_url=?",
                    (compact(result),ident,url))
                if updated.rowcount!=1:raise RelayError('terminal_state_changed')
                return {'ok':True,'state':'done'}
            if action=='blocked':
                if row['state'] in TERMINAL:return {'ok':True,'state':row['state']}
                code=reason if re.fullmatch(r'[a-z_]{1,60}',str(reason)) else 'worker_failed'
                state='uncertain' if row['state'] in ('intent','saved') else 'blocked'
                updated=self.db.execute(
                    "UPDATE requests SET state=?,reason=? WHERE id=? AND state=? AND lease=?",
                    (state,code,ident,row['state'],lease))
                if updated.rowcount!=1:raise RelayError('terminal_state_changed')
                return {'ok':True,'state':state}
            raise RelayError('invalid_transition')
    def cancel(self,ident,reason='client_disconnected'):
        """Lost client/claim acknowledgement: block before effect, uncertain after.

        Preserve a potentially already-clicked Send when a worker races the
        failed HTTP response. No transport switch, no silent retry.
        """
        if reason not in ('client_disconnected','claim_delivery_failed',
                          'relay_shutting_down'):
            raise RelayError('invalid_cancel_reason')
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.db.execute('SELECT state FROM requests WHERE id=?',(ident,)).fetchone()
                if row is None:raise RelayError('unknown_request')
                if row['state'] in TERMINAL:
                    self.db.execute('COMMIT')
                    return row['state']
                state='blocked' if row['state'] in ('queued','claimed') else 'uncertain'
                self.db.execute('UPDATE requests SET state=?,reason=? WHERE id=?',
                                (state,reason,ident))
                self.db.execute('COMMIT')
                return state
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
    def expire(self,ident):
        with self.lock:
            # Evaluate the current state within the write itself. A concurrent
            # DONE/UNCERTAIN transition may never be reverted by timeout.
            self.db.execute(
                "UPDATE requests SET state=CASE "
                "WHEN state IN ('queued','claimed') THEN 'blocked' "
                "ELSE 'uncertain' END,reason='request_timeout' "
                "WHERE id=? AND state NOT IN ('done','blocked','uncertain')",
                (ident,))

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,queue,key,timeout=1200,enable_playwright=False,
                 playwright_key=None):
        self.queue,self.key,self.request_timeout=queue,key,timeout
        self.enable_playwright=bool(enable_playwright)
        if self.enable_playwright:
            if not isinstance(playwright_key,str) or len(playwright_key)<24:
                raise RelayError('playwright_worker_key_required')
            if not isinstance(key,str) or hmac.compare_digest(playwright_key,key):
                raise RelayError('playwright_worker_key_not_distinct')
        self.playwright_key=playwright_key if self.enable_playwright else None
        self._waiters_lock=threading.Lock()
        self._waiters={}
        self._closing=threading.Event()
        self._handlers_lock=threading.Lock()
        self._handler_threads=set()
        self._handler_sockets=set()
        # Only applies to individual TCP receive/send operations; inference
        # waits may legitimately last much longer in the processing loop.
        self.ingress_timeout=10.0
        super().__init__(address,Handler)
    def process_request_thread(self,request,client_address):
        current=threading.current_thread()
        with self._handlers_lock:
            self._handler_threads.add(current)
            self._handler_sockets.add(request)
        try:
            request.settimeout(self.ingress_timeout)
            return super().process_request_thread(request,client_address)
        finally:
            with self._handlers_lock:
                self._handler_threads.discard(current)
                self._handler_sockets.discard(request)
    def handler_thread_count(self):
        with self._handlers_lock:return len(self._handler_threads)
    def server_close(self):
        # ThreadingHTTPServer daemon threads are NOT joined by default.
        # Wake HTTP wait loops before closing SQLite to avoid use-after-close.
        self._closing.set()
        # Request handlers may be blocked mid-header or mid-body. Wake their
        # socket reads immediately rather than waiting for an HTTP deadline,
        # then join before the caller closes the shared SQLite connection.
        with self._handlers_lock:
            active_sockets=list(self._handler_sockets)
        for client in active_sockets:
            try:client.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        super().server_close()
        deadline=time.monotonic()+5
        while True:
            with self._handlers_lock:
                live=[t for t in self._handler_threads if t.is_alive()
                      and t is not threading.current_thread()]
            if not live:break
            if time.monotonic()>=deadline:
                raise RuntimeError('relay_handlers_not_drained')
            for t in live:t.join(timeout=min(.1,max(0,deadline-time.monotonic())))
    def submit_and_attach(self,body,transport='extension',turn_id=None):
        # Queue identity resolution and HTTP waiter subscription share one
        # linearization lock with last-waiter cancellation. A retry cannot
        # attach in the gap between detach and cancellation.
        with self._waiters_lock:
            if self._closing.is_set():raise RelayError('relay_shutting_down')
            ident=self.queue.submit(body,transport=transport,turn_id=turn_id)
            self._waiters[ident]=self._waiters.get(ident,0)+1
            return ident
    def detach_waiter(self,ident,on_last=None,reason='client_disconnected'):
        if on_last not in (None,'cancel','expire'):
            raise RelayError('invalid_waiter_transition')
        with self._waiters_lock:
            count=self._waiters.get(ident,0)
            if count<=0:raise RelayError('waiter_accounting_invalid')
            if count==1:
                try:
                    if on_last=='cancel':
                        # Preserve the original call contract for existing
                        # callbacks and legacy tests.
                        if reason=='client_disconnected':self.queue.cancel(ident)
                        else:self.queue.cancel(ident,reason=reason)
                    elif on_last=='expire':self.queue.expire(ident)
                finally:
                    self._waiters.pop(ident,None)
                return 0
            self._waiters[ident]=count-1
            return count-1
    def waiter_count(self,ident):
        with self._waiters_lock:
            return self._waiters.get(ident,0)
    def active_waiter_count(self):
        with self._waiters_lock:
            return sum(self._waiters.values())
class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            return super().handle()
        except (OSError,TimeoutError) as exc:
            # An incomplete local HTTP request may time out or be interrupted
            # by shutdown before do_POST is entered. It owns no queue claim.
            # Unexpected application/I/O errors still propagate normally.
            if (self.server._closing.is_set() or
                    isinstance(exc,(TimeoutError,BrokenPipeError,
                                    ConnectionResetError,ConnectionAbortedError))):
                return
            raise
    def log_message(self,*args):pass
    def write_json(self,status,value):
        body=compact(value).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers()
        self.wfile.write(body)
    def error(self,status,code):self.write_json(status,{'error':{'message':code,'type':'relay_error','code':code}})
    def principal(self):
        """Explicitly separate Hermes/extension from the CDP bridge worker."""
        host=self.headers.get('Host','');port=self.server.server_address[1]
        if host not in (f'127.0.0.1:{port}',f'localhost:{port}'):return None
        auth=self.headers.get('Authorization','')
        if not auth.startswith('Bearer '):return None
        token=auth[7:]
        if token and hmac.compare_digest(token,self.server.key):
            return 'hermes'
        if (token and self.server.playwright_key and
                hmac.compare_digest(token,self.server.playwright_key)):
            return 'playwright_worker'
        return None
    def authorized(self):return self.principal()=='hermes'
    def peer_closed_after_body(self):
        """Best-effort EOF detection for a non-streaming HTTP response waiter.

        A fully closed TCP connection is readable with a zero-length peek.
        This never consumes request bytes or modifies the HTTP response.
        """
        try:
            readable,_,_=select.select([self.connection],[],[],0)
            if not readable:return False
            return self.connection.recv(1,socket.MSG_PEEK)==b''
        except (OSError,ValueError):
            return True
    def do_GET(self):
        if self.path=='/health':return self.write_json(200,{'ok':True,'service':'hermes-webgpt-relay','protocol':1,
                                                         'runtime_build':RUNTIME_BUILD})
        if not self.authorized():return self.error(401,'unauthorized')
        if self.path=='/v1/models':
            ids=[MODEL]+([MODEL_CDP] if self.server.enable_playwright else [])
            return self.write_json(200,{'object':'list','data':[
                {'id':model,'object':'model','owned_by':'local-web-relay'}
                for model in ids]})
        if self.path=='/bridge/status':
            counts=self.server.queue.status_counts()
            active=sum(counts[transport][state]
                       for transport in counts for state in ('claimed','intent','saved'))
            return self.write_json(200,{
                'ok':True,
                'transport_enabled':{'extension':True,
                                     'playwright':self.server.enable_playwright},
                'routes':counts,
                # This is a read-only age indicator, NOT worker liveness.
                'aged_active':self.server.queue.aged_active_counts(600),
                'aged_active_threshold_seconds':600,
                'aged_active_basis':'age_since_request_creation_not_worker_heartbeat',
                'active_requests':active,
                'active_http_waiters':self.server.active_waiter_count(),
                'max_active_requests':1,
            })
        return self.error(404,'not_found')
    def drain_rejected_post_body(self):
        """Best-effort bounded drain before a 401 on Windows TCP.

        Closing a socket with a fully transmitted but unread POST body can
        reset the connection and discard the authentication error response.
        Never buffer large untrusted bodies or wait for slow, incomplete data.
        """
        raw=self.headers.get('Content-Length','')
        if not re.fullmatch(r'[0-9]{1,7}',raw):return
        size=int(raw)
        if not 0<size<=MAX_BODY:return
        previous=self.connection.gettimeout()
        try:
            self.connection.settimeout(min(previous or .4,.4))
            self.rfile.read(size)
        except (OSError,TimeoutError):
            pass
        finally:
            try:self.connection.settimeout(previous)
            except OSError:pass
    def do_POST(self):
        principal=self.principal()
        if principal is None:
            self.drain_rejected_post_body()
            return self.error(401,'unauthorized')
        # Reject new work after shutdown starts, but accept final events from
        # existing leases so a completed browser response can still persist.
        if self.server._closing.is_set() and self.path!='/bridge/event':
            return self.error(503,'relay_shutting_down')
        waiter_registered=False
        abandoned=False
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>MAX_BODY:raise RelayError('invalid_body_size')
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise RelayError('object_body_required')
            if self.path=='/bridge/claim':
                transport=body.get('transport','extension')
                if transport not in ('extension','playwright'):raise RelayError('invalid_transport')
                if transport=='playwright' and not self.server.enable_playwright:
                    raise RelayError('playwright_not_enabled')
                expected='playwright_worker' if transport=='playwright' else 'hermes'
                if principal!=expected:return self.error(403,'wrong_transport_principal')
                job=self.server.queue.claim(body.get('worker'),transport=transport)
                # A shutdown can begin just after the first admission check.
                # Never deliver a freshly claimed job after that transition.
                if self.server._closing.is_set():
                    if job is not None:
                        self.server.queue.cancel(job['id'],reason='relay_shutting_down')
                    return self.error(503,'relay_shutting_down')
                try:
                    return self.write_json(200,{'ok':True,'job':job})
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                    # A claim is committed before its HTTP ACK; an aborted
                    # response can strand the single active slot forever.
                    # If a worker somehow raced ahead to intent, quarantine
                    # as uncertain instead of assuming no browser side effect.
                    if job is not None:
                        self.server.queue.cancel(job['id'],reason='claim_delivery_failed')
                    return
            if self.path=='/bridge/event':
                transport=self.server.queue.transport_for(body.get('id'))
                if transport=='playwright' and not self.server.enable_playwright:
                    raise RelayError('playwright_not_enabled')
                expected='playwright_worker' if transport=='playwright' else 'hermes'
                if principal!=expected:return self.error(403,'wrong_transport_principal')
                result=self.server.queue.event(body.get('id'),body.get('lease'),body.get('action'),body.get('tab'),
                                               body.get('url',''),body.get('text',''),body.get('reason',''),
                                               body.get('page_key'))
                return self.write_json(200,result)
            if self.path!='/v1/chat/completions':return self.error(404,'not_found')
            if principal!='hermes':return self.error(403,'worker_inference_forbidden')
            # No header means the original extension, preserving all existing
            # Hermes clients. CDP is per-request opt-in and never an automatic
            # retry/fallback for the same already-dispatched turn.
            transport=self.headers.get('X-Hermes-Transport')
            if transport is None:
                # Hermes can select this optional model even when its custom
                # provider drops non-standard headers.
                transport='playwright' if body.get('model')==MODEL_CDP else 'extension'
            if transport not in ('extension','playwright'):raise RelayError('invalid_transport')
            if body.get('model')==MODEL_CDP and transport!='playwright':
                raise RelayError('transport_model_mismatch')
            if transport=='playwright' and not self.server.enable_playwright:raise RelayError('playwright_not_enabled')
            turn_id=self.headers.get('X-Hermes-Turn-Id')
            # A TCP read EOF alone does NOT prove the client stopped receiving:
            # SHUT_WR is a legitimate request/response half-close. Only
            # clients that promise never to half-close may opt into early EOF
            # cancellation. Existing Hermes traffic remains conservative.
            peer_eof_cancel=self.headers.get('X-Hermes-Peer-EOF-Cancel')
            if peer_eof_cancel not in (None,'0','1'):
                raise RelayError('invalid_peer_eof_cancel')
            peer_eof_cancel=(peer_eof_cancel=='1')
            ident=self.server.submit_and_attach(body,transport=transport,turn_id=turn_id)
            waiter_registered=True
            deadline=time.monotonic()+self.server.request_timeout
            streaming=body.get('stream') is True
            if streaming:
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.end_headers()
            while True:
                if self.server._closing.is_set():
                    # Cooperative drain precedes Queue.close(), including
                    # blocked SSE/non-streaming callers. A possible Send
                    # becomes uncertain and is never replayed.
                    self.server.detach_waiter(
                        ident,on_last='cancel',reason='relay_shutting_down')
                    waiter_registered=False
                    return
                if not streaming and peer_eof_cancel and self.peer_closed_after_body():
                    # Disconnect from ONE HTTP waiter, not the whole logical
                    # turn while another client is still waiting on its ID.
                    self.server.detach_waiter(ident,on_last='cancel')
                    waiter_registered=False
                    return
                row=self.server.queue.read_status(ident)
                if row['state'] in TERMINAL:
                    row=self.server.queue.read(ident)
                    break
                if time.monotonic()>deadline:
                    remaining=self.server.detach_waiter(ident,on_last='expire')
                    waiter_registered=False
                    if remaining>0:
                        # This caller timed out, but another subscriber may
                        # still legitimately finish the SAME browser turn.
                        code='web_waiter_timeout'
                        if streaming:
                            self.wfile.write(('data: '+compact({'error':{
                                'message':code,'type':'relay_error','code':code
                            }})+'\n\n').encode())
                            self.wfile.flush()
                            return
                        return self.error(504,code)
                    row=self.server.queue.read(ident)
                    break
                if streaming:self.wfile.write(b': waiting for visible web reply\n\n');self.wfile.flush()
                time.sleep(min(.25,max(.01,self.server.request_timeout/10)))
            if row['state']!='done':
                code='web_'+str(row['reason'] or row['state'])
                if streaming:
                    self.wfile.write(('data: '+compact({'error':{'message':code,'type':'relay_error','code':code}})+'\n\n').encode());self.wfile.flush();return
                return self.error(504 if row['reason']=='request_timeout' else 502,code)
            result=json.loads(row['result'])
            if not streaming:return self.write_json(200,result)
            choice=result['choices'][0]
            chunk={k:result[k] for k in ('id','created','model')};chunk['object']='chat.completion.chunk'
            chunk['choices']=[{'index':0,'delta':choice['message'],'finish_reason':None}]
            if 'tool_calls' in choice['message']:
                chunk['choices'][0]['delta']['tool_calls']=[{**c,'index':i} for i,c in enumerate(choice['message']['tool_calls'])]
            self.wfile.write(('data: '+compact(chunk)+'\n\n').encode())
            chunk['choices']=[{'index':0,'delta':{},'finish_reason':choice['finish_reason']}]
            self.wfile.write(('data: '+compact(chunk)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
        except (ValueError,TypeError,KeyError,RecursionError) as exc:
            # If a client-side error occurs after queue admission, its waiter
            # must also detach so a different retry can continue safely.
            abandoned=waiter_registered
            try:
                status=(503 if isinstance(exc,RelayError) and
                        str(exc)=='relay_shutting_down' else 400)
                self.error(status,str(exc) if isinstance(exc,RelayError) else 'invalid_request')
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                abandoned=True
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            abandoned=True
        finally:
            if waiter_registered:
                # A last-waiter cancel is atomic with a concurrent new
                # subscriber's submit_and_attach.
                self.server.detach_waiter(ident,on_last='cancel' if abandoned else None)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);ap.add_argument('--port',type=int,default=18765)
    ap.add_argument('--enable-playwright',action='store_true',help='Opt in to per-request Playwright routing; legacy extension remains enabled')
    args=ap.parse_args();args.data.mkdir(parents=True,exist_ok=True)
    auth=args.data/'relay-auth.json'
    if not auth.exists():
        with auth.open('x',encoding='utf-8') as f:json.dump({'key':secrets.token_urlsafe(48)},f)
    key=json.loads(auth.read_text())['key']
    # Optional second principal, generated only on explicit CDP opt-in.
    # Existing extension and Hermes continue to use relay-auth.json.
    playwright_key=None
    if args.enable_playwright:
        worker_auth=args.data/'playwright-worker-auth.json'
        if not worker_auth.exists():
            with worker_auth.open('x',encoding='utf-8') as f:
                json.dump({'key':secrets.token_urlsafe(48)},f)
        playwright_key=json.loads(worker_auth.read_text(encoding='utf-8'))['key']
    queue=Queue(args.data/'relay.sqlite3')
    server=Server(('127.0.0.1',args.port),queue,key,
                  enable_playwright=args.enable_playwright,
                  playwright_key=playwright_key)
    print(compact({'ok':True,'port':server.server_address[1],'model':MODEL,
                   'playwright_route_enabled':server.enable_playwright,
                   'default_transport':'extension','max_active_requests':1}),flush=True)
    try:server.serve_forever()
    finally:server.server_close();queue.close()
if __name__=='__main__':main()
