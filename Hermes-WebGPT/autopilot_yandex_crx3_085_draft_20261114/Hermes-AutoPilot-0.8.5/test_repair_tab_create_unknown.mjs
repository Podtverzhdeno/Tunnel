import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

test('lost IDLE repair tab-create ACK cannot create a duplicate',async()=>{
  const db={},session={},tabs=new Map();
  let listener,onRemoved,created=0,failCreateAck=false;
  const hooks={addListener:()=>{}};
  const copy=(obj,k)=>Object.fromEntries((Array.isArray(k)?k:[k])
    .map(key=>[key,obj[key]===undefined?undefined:structuredClone(obj[key])]));
  const chrome={
    runtime:{id:'ap-idle-repair',onMessage:{addListener:fn=>listener=fn},
      onStartup:hooks,onInstalled:hooks},
    alarms:{create:()=>{},onAlarm:hooks},
    storage:{local:{get:async k=>copy(db,k),
      set:async v=>Object.assign(db,structuredClone(v))},
      session:{get:async k=>copy(session,k),
        set:async v=>Object.assign(session,structuredClone(v))}},
    tabs:{onRemoved:{addListener:fn=>onRemoved=fn},
      async create({url,active}){
        assert.equal(active,false);
        const tab={id:201+(++created),windowId:2,url,status:'complete'};
        tabs.set(tab.id,tab);
        if(failCreateAck){failCreateAck=false;throw Error('Lost create acknowledgement');}
        return structuredClone(tab);
      },
      async get(id){
        if(!tabs.has(id))throw Error('No tab with id');
        return structuredClone(tabs.get(id));
      },
      async remove(id){tabs.delete(id);}
    },
    scripting:{executeScript:async()=>{throw Error('No fake DOM in test');}}
  };
  globalThis.chrome=chrome;
  await import(pathToFileURL(path.join(process.cwd(),'background.js')).href+'?repair=missing-ack');
  const send=(type,extras={})=>new Promise(resolve=>listener({type,...extras},
    {id:chrome.runtime.id,url:'chrome-extension://ap-idle-repair/popup.html'},resolve));
  const tasks=[{id:'t1',prompt:'Synthetic work',acceptance:['receipt'],
    resources:['unit'],depends_on:[]}];
  const start=await send('START',{goal:'Repair tab once',tasks,maxConcurrent:3});
  assert.equal(start.ok,true,start.error);
  assert.equal(created,3);
  const run=db.hermesAutopilotRunV2;
  const idle=run.slots.find(s=>s.phase==='IDLE' && s.tabId!==null);
  assert.ok(idle,'fixture must have unassigned IDLE slot');
  const oldId=idle.tabId;
  tabs.delete(oldId);
  onRemoved(oldId);
  await send('STATUS'); // serializes after onRemoved through background's lock
  const marked=db.hermesAutopilotRunV2.slots.find(s=>s.id===idle.id);
  assert.equal(marked.phase,'MISSING_TAB');
  failCreateAck=true;
  const first=await send('REPAIR_IDLE',{workerId:idle.id});
  assert.equal(first.ok,false,'lost ACK cannot be treated as success');
  assert.equal(created,4,'exactly one repair tab was created');
  const marker=db['hermesAutopilotRepairCreateIntentV1_'+run.id+'_'+idle.id+'_'+oldId];
  assert.equal(marker?.state,'UNKNOWN_CREATE');
  const repeated=await send('REPAIR_IDLE',{workerId:idle.id});
  assert.equal(repeated.ok,false);
  assert.match(repeated.error,/TAB_CREATE_UNKNOWN_RECONCILIATION_REQUIRED/);
  assert.equal(created,4,'no second tab after an unknown creation');
});

test('repair cannot exceed configured concurrent worker limit',async()=>{
  const db={},session={},tabs=new Map();
  let listener,created=0;
  const hooks={addListener:()=>{}};
  const copy=(obj,k)=>Object.fromEntries((Array.isArray(k)?k:[k])
    .map(key=>[key,obj[key]===undefined?undefined:structuredClone(obj[key])]));
  const chrome={
    runtime:{id:'ap-repair-limit',onMessage:{addListener:fn=>listener=fn},
      onStartup:hooks,onInstalled:hooks},
    alarms:{create:()=>{},onAlarm:hooks},
    storage:{local:{get:async k=>copy(db,k),
      set:async v=>Object.assign(db,structuredClone(v))},
      session:{get:async k=>copy(session,k),
        set:async v=>Object.assign(session,structuredClone(v))}},
    tabs:{onRemoved:hooks,
      async create({url,active}){const t={id:300+(++created),url,status:'complete'};
        tabs.set(t.id,t);return t;},
      async get(id){if(!tabs.has(id))throw Error('No tab with id');return tabs.get(id);},
      async remove(id){tabs.delete(id);}
    },
    scripting:{executeScript:async()=>{throw Error('no composer');}}
  };
  globalThis.chrome=chrome;
  await import(pathToFileURL(path.join(process.cwd(),'background.js')).href+'?repair=limit');
  const send=(type,extras={})=>new Promise(resolve=>listener({type,...extras},
    {id:chrome.runtime.id,url:'chrome-extension://ap-repair-limit/popup.html'},resolve));
  const tasks=[{id:'t1',prompt:'Synthetic work',acceptance:['receipt'],
    resources:['unit'],depends_on:[]}];
  const start=await send('START',{goal:'Respect capacity',tasks,maxConcurrent:1});
  assert.equal(start.ok,true,start.error);
  const run=db.hermesAutopilotRunV2;
  const outside=run.slots.find((s,i)=>i>=run.maxConcurrent && s.phase==='AWAITING_TAB');
  assert.ok(outside,'fixture must include unallocated worker');
  const repair=await send('REPAIR_IDLE',{workerId:outside.id});
  assert.equal(repair.ok,false);
  assert.match(repair.error,/WORKER_OUTSIDE_CONCURRENCY_LIMIT/);
  assert.equal(created,1);
});
