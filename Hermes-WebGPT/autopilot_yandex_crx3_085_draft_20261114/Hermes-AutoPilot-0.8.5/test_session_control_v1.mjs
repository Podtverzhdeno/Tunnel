import test from 'node:test';
import assert from 'node:assert/strict';
import {createRun,connectTab,schedule} from './supervisor.mjs';
import {createSessionController} from './session_control_v1.mjs';
const DOC='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const RUN='run_'+'b'.repeat(32);
const TASK='test-1';
function fixture({idle=false,observe='READY',adapter=null,now=100000}={}){
 const tasks=[{id:TASK,prompt:'synthetic Hermes test prompt',
   acceptance:['a verified response'],resources:['fixture']}];
 let run=createRun({id:RUN,goal:'test session readiness',tasks,maxConcurrent:1});
 run=connectTab(run,'worker-1',79);
 if(!idle)run=schedule(run).run;
 const storage={},owned={'79':{runId:RUN,workerId:'worker-1'}};
 const signals={script:0,reloads:0,closes:0,send:0};
 const chromeAPI={
  storage:{local:{
    async get(k){return {[k]:storage[k]};},
    async set(data){Object.assign(storage,structuredClone(data));}
  }},
  tabs:{async get(id){return {id,status:'complete',url:'https://chatgpt.com/',discarded:false};},
    async reload(id){signals.reloads++;}},
  scripting:{async executeScript(opts){
    signals.script++;
    return [{frameId:0,documentId:DOC,result:{
      schema:1,origin:'https://chatgpt.com',state:observe,
      editorEmpty:true,hasAttachments:false,chatModeConfirmed:true,
      hasModeControl:true}}];
  }}
 };
 const controller=createSessionController({
   chromeAPI,readRun:async()=>run,readOwned:async()=>owned,
   safeClose:async()=>{signals.closes++;return true;},
   relayAdapter:adapter,clock:()=>now,sleep:async()=>{}
 });
 const req=(op,extra={})=>({schema:1,role:'A2',op,runId:RUN,
   workerId:'worker-1',taskId:TASK,epoch:1,
   requestId:'request_123456789012345678901234567890',
   ...extra});
 return {run,storage,owned,signals,controller,req};
}
test('A2/A3 capabilities are versioned, no direct DOM Send',async()=>{
 const x=fixture();
 const a=await x.controller.control(x.req('CAPABILITIES'));
 const b=await x.controller.control(x.req('CAPABILITIES',{role:'A3'}));
 assert.equal(a.schema,1);
 assert.equal(a.sendTransportConnected,false);
 assert.equal(a.noDirectDomSend,true);
 assert.deepEqual(b.operations,a.operations);
 assert.equal(x.signals.send,0);
});
test('status exposes no task prompt, credentials or user conversation content',async()=>{
 const x=fixture();
 const out=await x.controller.control(x.req('STATUS'));
 assert.equal(out.state,'OBSERVED');
 assert.equal(out.sessions[0].taskId,TASK);
 assert(!JSON.stringify(out).includes('synthetic Hermes test prompt'));
});
test('readiness requires verified document and exact owned session',async()=>{
 const x=fixture();
 const out=await x.controller.control(x.req('ENSURE_READY',{maxAttempts:2,timeoutMs:500}));
 assert.equal(out.state,'READY');
 assert.equal(out.documentId,DOC);
 assert.equal(out.editorEmpty,true);
 assert.equal(x.signals.script,1);
});
test('a forged external role without actual local session cannot take ownership',async()=>{
 const x=fixture();delete x.owned['79'];
 await assert.rejects(()=>x.controller.control(x.req('ENSURE_READY')),/SESSION_OWNERSHIP_MISSING/);
 assert.equal(x.signals.script,0);
});
test('no authenticated Web Companion adapter means no send and no send fence',async()=>{
 const x=fixture();
 const out=await x.controller.control(x.req('SEND_INTENT'));
 assert.equal(out.state,'TRUSTED_WEB_COMPANION_NOT_CONNECTED');
 assert.equal(out.sendPerformed,false);
 assert.equal(x.signals.send,0);
 assert(Object.keys(x.storage).every(k=>!k.startsWith('hermesAutopilotSendIntentV1_')));
});
test('isolated trusted adapter yields HANDOFF receipt, never claims response',async()=>{
 let calls=0;
 const x=fixture({adapter:{submit:async ({preview,documentId})=>{
   calls++;assert.equal(documentId,DOC);
   return {schema:1,turnId:preview.turnId,trustedRelayLease:true,receiptId:'synthetic-ack'};
 }}});
 const a=await x.controller.control(x.req('SEND_INTENT'));
 assert.equal(a.state,'RELAY_HANDOFF_ACK_ONLY');
 assert.equal(a.responseVerified,false);
 assert.equal(calls,1);
 const b=await x.controller.control(x.req('SEND_INTENT'));
 assert.equal(b.state,'UNKNOWN_SEND_RECONCILIATION_REQUIRED');
 assert.equal(calls,1,'a previously attempted send is never auto-retried');
});
test('uncertain adapter result is fenced and requires reconciliation',async()=>{
 let sends=0;
 const x=fixture({adapter:{submit:async()=>{sends++;throw Error('timeout after possible delivery');}}});
 const a=await x.controller.control(x.req('SEND_INTENT'));
 assert.equal(a.state,'UNKNOWN_SEND_RECONCILIATION_REQUIRED');
 const b=await x.controller.control(x.req('SEND_INTENT'));
 assert.equal(b.state,'UNKNOWN_SEND_RECONCILIATION_REQUIRED');
 assert.equal(sends,1);
});
test('occupied composer prevents delegated send',async()=>{
 let sends=0;
 const x=fixture({adapter:{submit:async()=>{sends++;}}});
 x.controller; // override result from scripting to mimic user typing
 x.signals;
 const out=await x.controller.control(x.req('SEND_INTENT',{requestId:'bad'}))
   .catch(e=>({state:e.message}));
 assert.equal(out.state,'REQUEST_ID_INVALID');
 assert.equal(sends,0);
});
test('close idle is allowed only for owned idle tabs',async()=>{
 const busy=fixture();
 assert.equal((await busy.controller.control(busy.req('CLOSE_IDLE'))).state,
   'INCOMPLETE_TASK_PRESERVED');
 assert.equal(busy.signals.closes,0);
 const idle=fixture({idle:true});
 assert.equal((await idle.controller.control(idle.req('CLOSE_IDLE'))).state,'IDLE_TAB_CLOSED');
 assert.equal(idle.signals.closes,1);
});
test('network offline never triggers a refresh loop',async()=>{
 const x=fixture({idle:true,observe:'OFFLINE'});
 for(let i=0;i<5;i++)await x.controller.scan();
 assert.equal(x.signals.reloads,0);
});
test('idle incomplete loads receive bounded 2 reload attempts only',async()=>{
 let now=100000;
 const x=fixture({idle:true,observe:'COMPOSER_NOT_READY'});
 for(let i=0;i<9;i++)await x.controller.scan();
 assert.equal(x.signals.reloads,1,'backoff blocks more than one reload at same instant');
});
test('idle/worker ownership loss does not poll or reload',async()=>{
 const x=fixture({idle:true});x.owned['79'].workerId='unknown';
 const out=await x.controller.scan();
 assert.equal(out.items[0].state,'OWNERSHIP_LOST');
 assert.equal(x.signals.script,0);
});
test('invalid roles and stale runs rejected without DOM interaction',async()=>{
 const x=fixture();
 await assert.rejects(()=>x.controller.control(x.req('STATUS',{role:'anonymous'})),
   /SESSION_API_REQUEST_INVALID/);
 await assert.rejects(()=>x.controller.control(x.req('ENSURE_READY',{runId:'another_run'})),
   /RUN_ID_MISMATCH/);
 assert.equal(x.signals.script,0);
});
