"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const {test}=require("node:test");
const source=fs.readFileSync(path.join(__dirname,"..","web-extension","background.js"),"utf8");
new vm.Script(source,{filename:"background.js"});

function extract(start,end){
  const a=source.indexOf(start),b=source.indexOf(end,a+start.length);
  assert(a>=0 && b>a, "expected source helpers must remain present");
  return source.slice(a,b);
}
const closeHelper=extract("async function hasAnotherTabInWindow(tabId){",
                          "async function recoverOrphansIfDue(){").trim();
function makeCloseHelper(tabs,{throwOnGet=false,throwOnQuery=false}={}){
  const chrome={tabs:{
    get:async id=>{if(throwOnGet)throw Error("tab vanished");return tabs.find(t=>t.id===id)||null;},
    query:async filter=>{if(throwOnQuery)throw Error("window closed");return tabs.filter(t=>t.windowId===filter.windowId);}
  }};
  return vm.runInNewContext("("+closeHelper+")",{chrome});
}
test("last tab in a window is not removed",async()=>{
  assert.equal(await makeCloseHelper([{id:7,windowId:2}])(7),false);
});
test("another tab in same window allows closure",async()=>{
  assert.equal(await makeCloseHelper([{id:7,windowId:2},{id:8,windowId:2}])(7),true);
});
test("another tab in a DIFFERENT window is not sufficient",async()=>{
  assert.equal(await makeCloseHelper([{id:7,windowId:2},{id:8,windowId:3}])(7),false);
});
test("vanished tabs, failed query and invalid IDs fail closed",async()=>{
  assert.equal(await makeCloseHelper([], {throwOnGet:true})(7),false);
  assert.equal(await makeCloseHelper([{id:7,windowId:2}],{throwOnQuery:true})(7),false);
  assert.equal(await makeCloseHelper([{id:7,windowId:2}])(-1),false);
});
test("both destructive closure paths use the guard",()=>{
  assert.match(source,/if\(eligible && await hasAnotherTabInWindow\(job\.tabId\)\)/);
  assert.match(source,/if\(!await hasAnotherTabInWindow\(task\.tabId\)\)/);
  assert.doesNotMatch(source,/chrome\.windows\.remove\s*\(/);
});
test("recovery scan is no longer unconditional on every 30s alarm",()=>{
  assert.match(source,/try\{await recoverOrphansIfDue\(\);\}/);
  assert.match(source,/RECOVERY_SCAN_INTERVAL_MS=120000/);
  assert.match(source,/chrome\.storage\.session\.set\(\{\[RECOVERY_SCAN_AT\]:Date\.now\(\)\}\)/);
});
test("recovery throttle scans first, skips immediate repeat, resumes later",async()=>{
  const saved={}; let runs=0; let now=2_000_000;
  const chrome={storage:{session:{
    get:async key=>({[key]:saved[key]}),
    set:async value=>Object.assign(saved,value)
  }}};
  const segment=extract("const RECOVERY_SCAN_INTERVAL_MS=120000;","async function hasAnotherTabInWindow(tabId){");
  const fnSrc=extract("async function recoverOrphansIfDue(){","let runningPoll=null;").trim();
  // Restrict extraction to helper body and exclude the following top-level declarations.
  const fn=fnSrc;
  assert.match(fn,/async function recoverOrphansIfDue/);
  const factory=new Function("chrome","Date","recoverOrphanedTabs",
     segment+"\n"+fn+"\nreturn recoverOrphansIfDue;");
  const recover=factory(chrome,{now:()=>now},async()=>{runs++;});
  await recover();await recover();assert.equal(runs,1);
  now+=120001;await recover();assert.equal(runs,2);
  saved.chatSmokeRecoveryScanAt=now+999999;await recover();assert.equal(runs,3);
});
