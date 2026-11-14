import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
const DOC='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
test('AutoPilot 0.8.5 respects three workers, drafts safely, reuses tabs, stops without loss',async()=>{
 const storage={},session={},tabs=new Map();
 let listener,alarmHandler,removeHandler,created=0,closed=0,drafts=0,sends=0,modeChecks=0;
 const events={addListener:()=>{}};
 const get=async (db,keys)=>{
   const list=Array.isArray(keys)?keys:[keys];
   return Object.fromEntries(list.map(key=>[key,db[key]===undefined?undefined:structuredClone(db[key])]));
 };
 const store=async (db,patch)=>Object.assign(db,structuredClone(patch));
 const chrome={
  runtime:{id:'autopilot-085-test',onMessage:{addListener:fn=>listener=fn},
    onStartup:events,onInstalled:events},
  alarms:{create:()=>{},onAlarm:{addListener:fn=>alarmHandler=fn}},
  tabs:{onRemoved:{addListener:fn=>removeHandler=fn},
    async create(opts){
      assert.equal(opts.url,'https://chatgpt.com/');
      assert.equal(opts.active,false);
      const tab={id:++created+100,windowId:7,status:'complete',url:opts.url};
      tabs.set(tab.id,tab);return {...tab};
    },
    async get(id){if(!tabs.has(id))throw Error('No tab with id');return {...tabs.get(id)};},
    async remove(id){if(!tabs.has(id))throw Error('No tab');tabs.delete(id);closed++;}
  },
  scripting:{async executeScript(opts){
    modeChecks++;
    if(opts.func.name==='inspectChatComposer'){
      return [{frameId:0,documentId:DOC,result:{
        schema:1,origin:'https://chatgpt.com',state:'READY',
        editorEmpty:true,hasAttachments:false,
        chatModeConfirmed:true,hasModeControl:true}}];
    }
    if(opts.func.name==='insertChatDraft'){
      drafts++;
      return [{frameId:0,documentId:DOC,result:{
        ok:true,status:'DRAFT_FILLED_NOT_SENT',submitted:false,
        textLength:opts.args[0].length}}];
    }
    throw Error('unexpected_executeScript_func:'+opts.func.name);
  }},
  storage:{local:{get:k=>get(storage,k),set:v=>store(storage,v)},
    session:{get:k=>get(session,k),set:v=>store(session,v)}}
 };
 globalThis.chrome=chrome;
 await import(pathToFileURL(path.join(process.cwd(),'background.js')).href);
 assert.equal(typeof listener,'function');
 const send=(type,data={})=>new Promise(resolve=>{
   const returned=listener({type,...data},
      {id:'autopilot-085-test',
       url:'chrome-extension://autopilot-085-test/popup.html'},resolve);
   assert.equal(returned,true);
 });
 const tasks=Array.from({length:5},(_,i)=>({
   id:'task-'+(i+1),prompt:'Synthetic prompt '+(i+1),
   acceptance:['Return evidence'],resources:['resource-'+i],
   depends_on:[]
 }));
 const started=await send('START',{goal:'Hermes autonomous diagnostic',tasks,maxConcurrent:3});
 assert.equal(started.ok,true,started.error);
 assert.equal(created,3,'maxConcurrent 3 should not create 5 empty tabs');
 assert.equal(started.result.mode,'AUTO_DRAFT_ONLY');
 assert.equal(started.result.messagingEnabled,false);
 assert.equal(drafts,2,'first bounded auto-draft pass');
 const stats=await send('STATUS');
 assert.equal(stats.ok,true);
 assert.equal(stats.result.browserSessions.schema,1);
 assert.equal(stats.result.run.slots.filter(x=>x.tabId!==null).length,3);
 assert.equal(stats.result.run.slots.filter(x=>x.taskId!==null).length,3);
 const idem=await send('START',{goal:'duplicate',tasks,maxConcurrent:5});
 assert.equal(idem.ok,false);
 assert.equal(created,3);
 const a2=await send('A2A_SESSION_V1',{request:{
   schema:1,role:'A2',op:'CAPABILITIES',runId:stats.result.run.id}});
 assert.equal(a2.ok,true,a2.error);
 assert.equal(a2.result.sendTransportConnected,false);
 const changed=await send('SET_CONCURRENCY',{maxConcurrent:5});
 assert.equal(changed.ok,true,changed.error);
 assert.equal(created,5);
 const again=await send('SET_CONCURRENCY',{maxConcurrent:5});
 assert.equal(again.ok,true);
 assert.equal(created,5);
 const stopped=await send('STOP');
 assert.equal(stopped.ok,true);
 assert.equal(stopped.result.stopped,true);
 assert.equal(closed,0,'assigned unfinished tasks must keep their tabs');
 assert.equal(tabs.size,5);
 assert(stopped.result.details.every(x=>x.reason==='INCOMPLETE_TASK_PRESERVED'));
 // Simulate MV3 browser-session proof loss while the old tabs still exist.
 session.hermesAutopilotOwnedTabsV2={};
 const refused=await send('START',{goal:'another run',tasks,maxConcurrent:3});
 assert.equal(refused.ok,false);
 assert.match(refused.error,/PREVIOUS_RUN_TABS_STILL_OPEN_REQUIRES_REVIEW/);
 assert.equal(created,5,'a stale run cannot accumulate new tabs');
 tabs.clear(); // Simulate the owner manually closing the preserved tabs.
 const restarted=await send('START',{goal:'after owner review',tasks,maxConcurrent:3});
 assert.equal(restarted.ok,true,restarted.error);
 assert.equal(created,8);
 const archives=Object.keys(storage).filter(k=>k.startsWith('hermesAutopilotArchivedRunV1_'));
 assert.equal(archives.length,1);
 assert.equal(storage[archives[0]].run.id,stats.result.run.id);
 assert.equal(sends,0,'AutoPilot never pressed Send or called a provider');
});
