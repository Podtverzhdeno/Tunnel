import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import {pathToFileURL} from "node:url";
test("lost chrome.tabs.create response never creates a duplicate or archives unknown tab",async()=>{
 const db={},session={},tabs=new Map();let listener,created=0,throwAck=true;
 const hooks={addListener:()=>{}};
 const copy=(obj,keys)=>Object.fromEntries((Array.isArray(keys)?keys:[keys])
   .map(k=>[k,obj[k]===undefined?undefined:structuredClone(obj[k])]));
 const chrome={
  runtime:{id:"ap-create-loss",onMessage:{addListener:f=>listener=f},
    onInstalled:hooks,onStartup:hooks},
  alarms:{create:()=>{},onAlarm:hooks},
  storage:{local:{get:async k=>copy(db,k),
    set:async v=>Object.assign(db,structuredClone(v))},
    session:{get:async k=>copy(session,k),
      set:async v=>Object.assign(session,structuredClone(v))}},
  tabs:{onRemoved:hooks,
    async create({url,active}){
      const id=++created+100;
      tabs.set(id,{id,windowId:3,url,status:"complete"});
      if(throwAck){throwAck=false;throw Error("Lost create ACK after actual tab creation");}
      return {...tabs.get(id)};
    },
    async get(id){
      if(!tabs.has(id))throw Error("No tab with id");
      return {...tabs.get(id)};
    },
    async remove(id){tabs.delete(id)}
  },
  scripting:{executeScript:async()=>{throw Error("no test composer")}}
 };
 globalThis.chrome=chrome;
 await import(pathToFileURL(path.join(process.cwd(),"background.js")).href+"?negative=create");
 const send=(type,extra={})=>new Promise(resolve=>listener(
   {type,...extra},{id:chrome.runtime.id,
     url:"chrome-extension://ap-create-loss/popup.html"},resolve));
 const tasks=[{id:"t1",prompt:"Synthetic",acceptance:["proof"],
   depends_on:[],resources:["unit"]}];
 const started=await send("START",{goal:"lost tab create acknowledgement",tasks,maxConcurrent:1});
 assert.equal(started.ok,false);
 assert.equal(created,1);
 assert.equal(db.hermesAutopilotRunV2.status,"ERROR_START");
 const runId=db.hermesAutopilotRunV2.id;
 const marker=Object.entries(db).find(([key])=>
   key.startsWith("hermesAutopilotTabCreateIntentV1_"+runId));
 assert(marker);
 assert.equal(marker[1].state,"UNKNOWN_CREATE");
 const retry=await send("RETRY_START");
 assert.equal(retry.ok,false);
 assert.match(retry.error,/TAB_CREATE_UNKNOWN_RECONCILIATION_REQUIRED/);
 assert.equal(created,1);
 const stopped=await send("STOP");
 assert.equal(stopped.ok,true);
 const next=await send("START",{goal:"must remain blocked",tasks,maxConcurrent:1});
 assert.equal(next.ok,false);
 assert.match(next.error,/PREVIOUS_TAB_CREATE_OUTCOME_UNKNOWN/);
 assert.equal(created,1,"lost ACK cannot accumulate another tab");
 assert.equal(tabs.size,1);
});
