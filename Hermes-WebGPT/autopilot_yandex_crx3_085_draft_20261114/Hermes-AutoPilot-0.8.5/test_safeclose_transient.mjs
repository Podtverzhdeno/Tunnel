import test from "node:test";
import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
import path from "node:path";
test("transient tabs.get must NOT delete owned IDLE tab lease",async()=>{
 const db={},session={},tabs=new Map();
 let next=100,listener,failingId=null,removeCalls=0;
 const events={addListener:()=>{}};
 const get=(target,keys)=>Object.fromEntries((Array.isArray(keys)?keys:[keys])
  .map(k=>[k,target[k]===undefined?undefined:structuredClone(target[k])]));
 const chrome={
  runtime:{id:"ap-transient",onMessage:{addListener:f=>listener=f},
    onStartup:events,onInstalled:events},
  alarms:{create:()=>{},onAlarm:events},
  storage:{local:{get:async k=>get(db,k),
                 set:async v=>Object.assign(db,structuredClone(v))},
    session:{get:async k=>get(session,k),
             set:async v=>Object.assign(session,structuredClone(v))}},
  tabs:{onRemoved:events,
    async create({url,active}){
      assert.equal(active,false);
      const tab={id:++next,windowId:5,status:"complete",url};
      tabs.set(tab.id,tab);return structuredClone(tab);
    },
    async get(id){
      if(id===failingId)throw Error("Temporary browser transport failure");
      if(!tabs.has(id))throw Error("No tab with id");
      return structuredClone(tabs.get(id));
    },
    async remove(id){removeCalls++;tabs.delete(id);}
  },
  scripting:{executeScript:async()=>{throw Error("No registered composer in test");}}
 };
 globalThis.chrome=chrome;
 await import(pathToFileURL(path.join(process.cwd(),"background.js")).href+"?negative=transient");
 const send=(type,data={})=>new Promise(resolve=>
   listener({type,...data},{id:chrome.runtime.id,
     url:"chrome-extension://ap-transient/popup.html"},resolve));
 const tasks=[{id:"task1",prompt:"Synthetic",acceptance:["Proof"],
   depends_on:[],resources:["fixture"]}];
 const started=await send("START",{goal:"preserve owned idle",tasks,maxConcurrent:3});
 assert.equal(started.ok,true,started.error);
 const run=db.hermesAutopilotRunV2;
 const idle=run.slots.find(s=>s.phase==="IDLE"&&s.tabId!==null);
 const moved=run.slots.find(s=>s.phase==="IDLE"&&s.tabId!==null&&s.id!==idle?.id);
 assert(moved,"expected a second idle tab");
 tabs.get(moved.tabId).url="https://chatgpt.com/auth/login";
 assert(idle,"expected a second IDLE tab");
 failingId=idle.tabId;
 const ownerBefore=session.hermesAutopilotOwnedTabsV2[String(idle.tabId)];
 assert.equal(ownerBefore.workerId,idle.id);
 const stopped=await send("STOP");
 assert.equal(stopped.ok,true,stopped.error);
 assert.equal(stopped.result.stopped,true);
 assert(stopped.result.details.some(d=>d.workerId===idle.id&&d.closed===false));
 assert(stopped.result.details.some(d=>d.workerId===moved.id&&d.closed===false));
 assert.equal(removeCalls,0);
 assert.equal(session.hermesAutopilotOwnedTabsV2[String(idle.tabId)].workerId,idle.id);
 assert.equal(tabs.has(idle.tabId),true);
 failingId=null;
 const another=await send("START",{goal:"duplicate run",tasks,maxConcurrent:2});
 assert.equal(another.ok,false);
 assert.match(another.error,/PREVIOUS_RUN_TABS_STILL_OPEN_REQUIRES_REVIEW/);
 assert.equal(tabs.size,3);
});
