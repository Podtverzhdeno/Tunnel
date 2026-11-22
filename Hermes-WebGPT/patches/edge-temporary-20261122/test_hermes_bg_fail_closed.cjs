/* Zero-network VM regression of REAL Hermes Web Companion 0.9.7 BG staging.
 * Deliberately no automatic Send; confirms exact temporary URL and at-most one
 * claimed tab. It is NOT a production transport or a ChatGPT E2E test.
 */
"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const source=fs.readFileSync(
  path.join(__dirname,"hermes_relay_bg_fail_closed.js"),"utf8");
const JOB="11111111-1111-4111-8111-111111111111";
const storage={"hermesRelayEnabled_v1":true,"hermesRelayWorker_v1":"synthetic-worker"};
const events={};
const requests=[],tabs=new Map(),writes=[];
let claimed=false,removed=0,scriptInjected=0;
const clone=x=>x===undefined?undefined:JSON.parse(JSON.stringify(x));
const local={
  get:async key=>typeof key==="string"?{[key]:clone(storage[key])}:
    Object.fromEntries((key||[]).map(k=>[k,clone(storage[k])])),
  set:async payload=>{
    Object.assign(storage,clone(payload));
    writes.push(clone(payload));
  }
};
const chrome={
  runtime:{
    id:"test",
    getURL:name=>"chrome-extension://test/"+name,
    onMessage:{addListener:f=>events.onMessage=f},
  },
  storage:{local},
  windows:{
    getLastFocused:async()=>({id:7,type:"normal"}),
    getAll:async()=>[{id:7,type:"normal"}],
    get:async id=>({id,type:"normal"})
  },
  tabs:{
    create:async options=>{
      const tab={id:301,windowId:7,status:"loading",...options};
      tabs.set(301,tab);return clone(tab);
    },
    get:async id=>clone(tabs.get(id)),
    remove:async()=>{removed++;throw Error("no_tab_removal_allowed_in_fixture");},
    onUpdated:{addListener:f=>events.onUpdated=f}
  },
  scripting:{executeScript:async()=>{scriptInjected++;throw Error("injection_not_expected");}},
  alarms:{
    create:(name,config)=>{events.alarmConfig={name,...config};},
    onAlarm:{addListener:f=>events.onAlarm=f}
  }
};
const fetch=async(uri,options={})=>{
  if(uri.startsWith("chrome-extension:"))
    return{ok:true,json:async()=>({endpoint:"http://127.0.0.1:18765",key:"x".repeat(40)})};
  const route=new URL(uri).pathname;
  const body=JSON.parse(options.body||"{}");
  requests.push({route,action:body.action||null});
  if(route==="/bridge/claim"){
    const job=!claimed?{id:JOB,lease:"lease-fixture",prompt:"DO NOT SEND"}:null;
    claimed=true;
    return {ok:true,json:async()=>({job})};
  }
  if(route==="/bridge/event")
    return {ok:true,json:async()=>({ok:true,state:"claimed",tab:body.tab??null})};
  if(route==="/v1/models")
    return {ok:true,json:async()=>({data:[]})};
  throw Error("unexpected_endpoint_"+route);
};
vm.runInNewContext(source,{
  chrome,fetch,URL,AbortSignal,
  crypto:{randomUUID:()=>JOB},
  Date,console:{warn(){}},
  setTimeout:()=>0,
  // deterministic no timers; VM owns no DOM
});
async function flush(){
  for(let i=0;i<90;i++)await new Promise(resolve=>setImmediate(resolve));
}
async function message(type,extra={}){
  return new Promise(resolve=>{
    const ok=events.onMessage({type,id:JOB,...extra},
      {id:"test",tab:{id:301,windowId:7},documentId:"synthetic-doc",frameId:0},
      resolve);
    assert.equal(ok,true);
  });
}
(async()=>{
  assert.equal(events.alarmConfig.periodInMinutes,0.5);
  events.onAlarm({name:"hermes-relay-poll"});
  await flush();
  assert.equal(tabs.size,1,"creates one tab, no fan-out");
  const tab=tabs.get(301);
  assert.equal(tab.url,"https://chatgpt.com/?temporary-chat=true");
  assert.equal(tab.active,false,"Hermes task doesn't interrupt user tab");
  assert.equal(storage.hermesRelayActive_v1.job.id,JOB);
  assert.equal(storage.hermesRelayActive_v1.state,"created");
  assert.equal(requests.filter(r=>r.route==="/bridge/claim").length,1);
  const denied=await message("hermes-relay-get");
  assert.equal(denied.ok,false,"prompt remains inaccessible");
  assert.equal(denied.reason,"temporary_transport_v2_not_integrated");
  const intent=await message("hermes-relay-intent");
  assert.equal(intent.ok,false,"server Send intent refused");
  const preflight=await message("hermes-relay-preflight");
  assert.equal(preflight.ok,false,"preflight refused");
  const done=await message("hermes-relay-finished",{
    status:"done",userSeen:true,assistantSeen:true,
    responseText:"fake",url:"https://chatgpt.com/c/wrong"});
  assert.equal(done.ok,false,"cannot fake ordinary-chat result for Temporary");
  assert.equal(removed,0,"never remove session tab in temporary staging");
  assert.equal(scriptInjected,0,"does not inject runnable content script");
  assert.equal(requests.some(r=>r.action==="intent"||r.action==="result"),false,
    "no side effects indicating a model Send/result");
  events.onAlarm({name:"hermes-relay-poll"});
  await flush();
  assert.equal(tabs.size,1,"duplicate alarm never opens second tab for active job");
  assert.equal(requests.filter(r=>r.route==="/bridge/claim").length,1);
  console.log("PASS: real Hermes BG staged opening exact Temporary URL, no Send, no result, no close or duplicate claim");
})().catch(e=>{console.error(e);process.exitCode=1});
