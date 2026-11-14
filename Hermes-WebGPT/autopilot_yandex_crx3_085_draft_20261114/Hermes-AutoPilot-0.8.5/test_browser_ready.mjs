import test from 'node:test';
import assert from 'node:assert/strict';
import {ensureChatReady} from './browser_ready.mjs';

const DOC='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
function runtime(states,{url='https://chatgpt.com/',status='complete',documentId=DOC}={}){
 const calls=[];let current=0,owned=true,reloads=0;
 const chromeAPI={
  tabs:{get:async id=>({id,url,status,discarded:false}),
    reload:async()=>{reloads++;}},
  scripting:{executeScript:async opts=>{
    calls.push(opts);
    const state=states[Math.min(current++,states.length-1)];
    if(state instanceof Error)throw state;
    return [{frameId:0,documentId,result:{schema:1,origin:'https://chatgpt.com',
      state,editorEmpty:true,hasAttachments:false,
      chatModeConfirmed:state==='READY',hasModeControl:true}}];
  }}
 };
 return {chromeAPI,calls,setOwned:value=>owned=value,assertOwned:async()=>owned,
  getReloads:()=>reloads};
}
const run=async(r,options={})=>ensureChatReady({
 chromeAPI:r.chromeAPI,tabId:11,assertOwned:r.assertOwned,
 sleep:async()=>{},maxAttempts:5,timeoutMs:2500,...options
});
test('Work-to-Chat switch occurs once and requires subsequent ready confirmation',async()=>{
 const r=runtime(['MODE_SWITCH_REQUESTED','READY']);
 const result=await run(r);
 assert.equal(result.state,'READY');
 assert.equal(result.attempts,2);
 assert.equal(result.documentId,DOC);
 assert.deepEqual(r.calls.map(x=>x.args[0].switchToChat),[true,false]);
});
test('without an explicit mode-switch grant Work is never clicked',async()=>{
 const r=runtime(['WORK_MODE']);
 const result=await run(r,{allowModeSwitch:false,maxAttempts:2});
 assert.equal(result.state,'CHAT_READY_TIMEOUT');
 assert(r.calls.every(x=>x.args[0].switchToChat===false));
});
test('offline returns terminal observation; no Send',async()=>{
 const r=runtime(['OFFLINE']);
 const result=await run(r);
 assert.equal(result.state,'OFFLINE');
 assert.equal(r.calls.length,1);
});
test('login required is not mistaken for a ready composer',async()=>{
 assert.equal((await run(runtime(['AUTH_REQUIRED']))).state,'AUTH_REQUIRED');
});
test('non-chatgpt URL rejected without reading or modifying DOM',async()=>{
 const r=runtime(['READY'],{url:'https://chatgpt.com.evil.invalid/'});
 assert.equal((await run(r)).state,'FOREIGN_TAB');
 assert.equal(r.calls.length,0);
});
test('no documentId fails closed',async()=>{
 const r=runtime(['READY'],{documentId:null});
 assert.equal((await run(r)).state,'DOCUMENT_NOT_PROVEN');
});
test('ambiguous composer fails closed',async()=>{
 const r=runtime(['AMBIGUOUS_COMPOSER']);
 assert.equal((await run(r)).state,'AMBIGUOUS_COMPOSER');
});
test('loading waits without injecting and then stops at a bounded attempt count',async()=>{
 const r=runtime(['READY'],{status:'loading'});
 const result=await run(r,{maxAttempts:3,timeoutMs:2500});
 assert.equal(result.state,'CHAT_READY_TIMEOUT');
 assert.equal(r.calls.length,0);
 assert.equal(result.attempts,3);
});
test('scripting failure does not become ready',async()=>{
 const r=runtime([Error('boom')]);
 const result=await run(r,{maxAttempts:2});
 assert.equal(result.state,'CHAT_READY_TIMEOUT');
 assert.equal(result.lastObservation,'DOM_PROBE_FAILED');
});
test('ownership lost blocks even after successful DOM readback',async()=>{
 const r=runtime(['READY']);
 let n=0;
 r.assertOwned=async()=>++n===1;
 assert.equal((await run(r)).state,'OWNERSHIP_LOST');
});
test('no result from chrome.scripting is not accepted',async()=>{
 const r=runtime(['READY']);
 r.chromeAPI.scripting.executeScript=async()=>[];
 assert.equal((await run(r)).state,'DOCUMENT_NOT_PROVEN');
});
test('no automatic ChatGPT send, refresh, or tab creation is invoked',async()=>{
 const r=runtime(['READY']);
 const out=await run(r);
 assert.equal(out.state,'READY');
 assert.equal(r.getReloads(),0);
 assert.equal(r.calls.length,1);
});
