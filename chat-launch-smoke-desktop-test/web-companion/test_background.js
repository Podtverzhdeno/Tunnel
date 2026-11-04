/* Service worker bounded-fanout regression (mock chrome APIs, no real chats). */
"use strict";
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const source=fs.readFileSync(__dirname+"/background.js","utf8");
const idAt=i=>"01234567-89ab-4cde-8901-"+String(i).padStart(12,"0");
async function simulate({count=10,windows=1,longPrompt=false,completion=false,
  realCap=false,removeFails=false,emitRemovedDuringRemove=false}={}){
  const events={},stored={},local={},tabs=new Map(),logs=[],ackById=new Map(),results=[],tabIdentity=new Map(),acceptedById=new Map();
  let failRemove=removeFails;
  let now=1000000;
  let queue=Array.from({length:count},(_,i)=>({
    id:idAt(i),prompt:longPrompt?"Long research task "+("x".repeat(220))+i:"agent "+i,
    ...(longPrompt?{}:{minHoldSeconds:120}),
    ...(completion?{completion_mode:"report",report_name:"completion-"+idAt(i)+".md"}:{})
  }));
  const chrome={
    runtime:{getURL:x=>"chrome-extension://test/"+x,
      onInstalled:{addListener:f=>events.installed=f},
      onStartup:{addListener:f=>events.startup=f},
      onMessage:{addListener:f=>events.message=f}},
    storage:{session:{
      get:async key=>{
        if(key===null)return {...stored};
        return {[key]:stored[key]};
      },
      set:async obj=>Object.assign(stored,obj),
      remove:async key=>{delete stored[key];}
    },local:{
      get:async keys=>{
        if(keys===null)return {...local};
        if(Array.isArray(keys))return Object.fromEntries(keys.map(k=>[k,local[k]]));
        return {[keys]:local[keys]};
      },
      set:async obj=>Object.assign(local,obj),
       remove:async key=>{delete local[key];}
    }},
    alarms:{create(){},onAlarm:{addListener:f=>events.alarm=f}},
    windows:{getAll:async()=>Array.from({length:windows},(_,i)=>({id:100+i}))},
    tabs:{
      query:async()=>[...tabs.values()].map(t=>({...t,url:t.url.startsWith("https://chatgpt.com/c/")?t.url:"https://chatgpt.com/c/test"+t.id})),
      create:async props=>{
        const id=300+logs.filter(x=>x.type==="opened").length;
        tabs.set(id,{...props,id});
        logs.push({type:"opened",id,active:props.active});
        return{id};
      },
      get:async id=>tabs.has(id)?{...tabs.get(id),url:
        tabs.get(id).url.startsWith("https://chatgpt.com/c/")?
          tabs.get(id).url:"https://chatgpt.com/c/test"+id}:null,
      remove:async id=>{
        if(failRemove)throw Error("simulated_remove_failed");
        tabs.delete(id);logs.push({type:"closed",id});
        if(emitRemovedDuringRemove&&events.removed){
          events.removed(id);
          // Browser fires onRemoved before tabs.remove's Promise settles.
          for(let n=0;n<4;n++)await new Promise(r=>setImmediate(r));
        }
      },
      onRemoved:{addListener:f=>events.removed=f}
    },
    scripting:{executeScript:async({target})=>[
      {result:{id:tabIdentity.get(target.tabId)||idAt(target.tabId-300)}}
    ]}
  };
  const fetch=async(url,options={})=>{
    if(url.endsWith("/bridge-config.json"))return{ok:true,json:async()=>({
      endpoint:"http://127.0.0.1:18763",key:"x".repeat(40)})};
    if(url.includes("/v1/ack?id=")){
      const id=new URL(url).searchParams.get("id");
      return {ok:true,json:async()=>({ok:true,ack:ackById.get(id)||null,pending:"report_not_found"})};
    }
    if(url.includes("/v1/close-eligible?id=")){
      const id=new URL(url).searchParams.get("id");
      const accepted=acceptedById.get(id);
      return{ok:true,json:async()=>({
        eligible:accepted?.eligible===true,reason:"receipt_done",
        ...(accepted?.eligible?{url:accepted.url}:{})
      })};
    }
    if(url.endsWith("/v1/next"))return{ok:true,json:async()=>({
      job:queue.shift()||null})};
    if(url.endsWith("/v1/result")){
      const receipt=JSON.parse(options.body);
      results.push(receipt);
      acceptedById.set(receipt.id,{eligible:true,url:receipt.url});
    }
    if(url.endsWith("/v1/event")){
      const event=JSON.parse(options.body);
      logs.push({type:"event",stage:event.stage,reason:event.reason||""});
      return{ok:true,json:async()=>({ok:true})};
    }
    if(url.endsWith("/v1/heartbeat")||url.endsWith("/v1/result"))
      return{ok:true,json:async()=>({ok:true})};
    throw Error("unexpected endpoint "+url);
  };
  const Clock=class extends Date {static now(){return now;}};
  const ctx={chrome,fetch,console:{warn(){}},Date:Clock,Promise,
    setTimeout:fn=>setImmediate(fn)};
  vm.runInNewContext(realCap?source:source.replace("const CAP=25;","const CAP=2;"),ctx);
  // Wait for all async IO in the mocked poll, without external timers.
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  return{events,stored,local,tabs,logs,queue,ackById,results,tabIdentity,
    advance(ms){now+=ms;},
    setRemoveFails(value){failRemove=Boolean(value);},
    setCloseEligible(id,value){
      const receipt=acceptedById.get(id);
      if(receipt)acceptedById.set(id,{...receipt,eligible:Boolean(value)});
    },
    setCloseUrl(id,value){
      const receipt=acceptedById.get(id);
      if(receipt)acceptedById.set(id,{...receipt,url:value});
    }};
}
async function send(ext,message,tabId){
  return new Promise(resolve=>{
    const ok=ext.events.message(message,{tab:{id:tabId}},resolve);
    assert.equal(ok,true);
  });
}
(async()=>{
  const ext=await simulate();
  assert.equal(ext.tabs.size,2);
  assert.equal(ext.logs.filter(x=>x.type==="opened"&&x.active===false).length,2);
  assert.equal(ext.queue.length,8);
  const one=idAt(0);
  assert.equal((await send(ext,{type:"chat-smoke-stage",id:one,
    stage:"send_ui_diagnostic",reason:"button_disabled"},300)).ok,true);
  assert.equal(ext.logs.some(x=>x.type==="event"&&x.stage==="send_ui_diagnostic"&&
    x.reason==="button_disabled"),true);
  assert.equal((await send(ext,{type:"chat-smoke-stage",id:one,
    stage:"send_ui_diagnostic",reason:"private_token_visible"},300)).ok,false,
    "diagnostic enum rejects unbounded/private content");
  assert.equal((await send(ext,{type:"chat-smoke-get",id:one},300)).ok,true);
  assert.equal((await send(ext,{type:"chat-smoke-get",id:one},301)).ok,false);
  const msg=await send(ext,{type:"chat-smoke-finished",id:one,
    status:"submitted",userSeen:true,reason:"submitted_after_min_hold",
    url:"https://chatgpt.com/c/abc123"},300);
  assert.equal(msg.reported,"submitted");
  for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r));
  assert.equal(ext.tabs.size,2,"new job fills the single available slot");
  assert.equal(ext.logs.filter(x=>x.type==="closed").length,1);
  assert.equal(Object.keys(ext.stored).filter(k=>k.startsWith("chatSmokeTask_")).length,2);
  const gated=await simulate({count:2,completion:true});
  const idG=idAt(0);
  const assigned=await send(gated,{type:"chat-smoke-get",id:idG},300);
  assert.equal(assigned.completionMode,true);
  const stage=await send(gated,{type:"chat-smoke-stage",id:idG,
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  assert.equal(stage.ok,true);
  // Mock a verified SPA route transition on this task-owned tab.
  gated.tabs.get(300).url="https://chatgpt.com/c/abcde";
  const submitted=await send(gated,{type:"chat-smoke-finished",id:idG,
    status:"done",assistantSeen:true,userSeen:true,reason:"assistant_confirmed",
    url:"https://chatgpt.com/c/abcde"},300);
  assert.equal(submitted.reported,"awaiting_report_ack");
  for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));
  assert.equal(gated.tabs.size,2,"model DOM completion is not an accepted report");
  // Bad or cross-job receipt cannot close the task-owned tab.
  gated.ackById.set(idG,{status:"REPORT_ACKED",job_id:idAt(1),
    trust_level:"advisory_artifact_integrity_only",ack_id:"a".repeat(32)});
  gated.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  assert.equal(gated.tabs.size,2,"wrong-job ack is rejected");
  gated.ackById.set(idG,{status:"REPORT_ACKED",job_id:idG,
    trust_level:"advisory_artifact_integrity_only",ack_id:"a".repeat(32)});
  gated.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  assert.equal(gated.tabs.has(300),false,"report ACK closes only its owned tab");
  assert.equal(gated.tabs.has(301),true,"unacknowledged second agent stays open");
  assert.equal(gated.results[0].ack_id,"a".repeat(32));
  assert.equal(gated.results[0].status,"done");
  assert.equal(gated.local["chatSmokeOwned_"+idG],undefined,
    "successful completion clears the durable binding");
  // The initial assignment and message_saved are persisted even before ACK.
  const persistent=await simulate({count:1,completion:true});
  assert.equal(persistent.local["chatSmokeOwned_"+idG].tabId,300);
  assert.equal(persistent.local["chatSmokeOwned_"+idG].savedUrl,"");
  await send(persistent,{type:"chat-smoke-stage",id:idG,
    stage:"message_saved",url:"https://chatgpt.com/c/agent-persist?model=hidden"},300);
  assert.equal(persistent.local["chatSmokeOwned_"+idG].savedUrl,
    "https://chatgpt.com/c/agent-persist");
  // A navigated or reused tab must not send a completion receipt.
  const impostor=await simulate({count:1,completion:true});
  await send(impostor,{type:"chat-smoke-stage",id:idG,
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  impostor.tabs.get(300).url="https://chatgpt.com/c/abcde";
  impostor.tabIdentity.set(300,idAt(1));
  impostor.ackById.set(idG,{status:"REPORT_ACKED",job_id:idG,
    trust_level:"advisory_artifact_integrity_only",ack_id:"a".repeat(32)});
  impostor.events.alarm({name:"chat-smoke-queue"});
  for(let j=0;j<60;j++)await new Promise(r=>setImmediate(r));
  assert.equal(impostor.tabs.has(300),true,"wrong DOM identity leaves tab open");
  assert.equal(impostor.results.length,0,
    "wrong DOM identity must not finalize even a valid-looking ACK");
  // A copied duplicate agent chat is ambiguous and must not close either tab.
  const duplicate=await simulate({count:2,completion:true});
  await send(duplicate,{type:"chat-smoke-stage",id:idG,
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  duplicate.tabs.get(300).url="https://chatgpt.com/c/abcde";
  duplicate.tabs.get(301).url="https://chatgpt.com/c/other-chat";
  duplicate.tabIdentity.set(301,idG);
  duplicate.ackById.set(idG,{status:"REPORT_ACKED",job_id:idG,
    trust_level:"advisory_artifact_integrity_only",ack_id:"a".repeat(32)});
  duplicate.events.alarm({name:"chat-smoke-queue"});
  for(let j=0;j<60;j++)await new Promise(r=>setImmediate(r));
  assert.equal(duplicate.tabs.has(300),true,"duplicate owned chat stays open");
  assert.equal(duplicate.results.length,0,
    "duplicate owned chat must not finalize twice");
  // Disputed SPA route update cannot replace a durable saved URL silently.
  await send(persistent,{type:"chat-smoke-stage",id:idG,
    stage:"message_saved",url:"https://chatgpt.com/c/other-chat"},300);
  assert.equal(persistent.local["chatSmokeOwned_"+idG].needsReconciliation,true);
  assert.equal(persistent.local["chatSmokeOwned_"+idG].savedUrl,
    "https://chatgpt.com/c/agent-persist");
  const privateUrl=await simulate({count:1,completion:true});
  const privateId=idAt(0);
  const route="https://chatgpt.com/c/abcde?session=PRIVATE_TOKEN";
  await send(privateUrl,{type:"chat-smoke-stage",id:privateId,
    stage:"message_saved",url:route},300);
  privateUrl.tabs.get(300).url=route;
  privateUrl.ackById.set(privateId,{status:"REPORT_ACKED",job_id:privateId,
    trust_level:"advisory_artifact_integrity_only",ack_id:"d".repeat(32)});
  privateUrl.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<65;i++)await new Promise(r=>setImmediate(r));
  assert.equal(privateUrl.results.length,1);
  assert.equal(privateUrl.results[0].url,"https://chatgpt.com/c/abcde",
    "private URL query parameters must never be written into result");
  assert.equal(privateUrl.logs.some(x=>x.type==="closed"),true);

  const removal=await simulate({count:1,completion:true,removeFails:true});
  await send(removal,{type:"chat-smoke-stage",id:privateId,
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  removal.tabs.get(300).url="https://chatgpt.com/c/abcde";
  removal.ackById.set(privateId,{status:"REPORT_ACKED",job_id:privateId,
    trust_level:"advisory_artifact_integrity_only",ack_id:"e".repeat(32)});
  removal.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  assert.equal(removal.results.length,1,
    "only one ACK-bound server completion receipt is submitted");
  assert.equal(removal.tabs.has(300),true);
  assert.ok(removal.local["chatSmokeOwned_"+privateId],
    "failed tab removal must not discard durable ownership record");
  assert.equal(removal.stored["chatSmokeTask_"+privateId].receiptAccepted,true);
  removal.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<50;i++)await new Promise(r=>setImmediate(r));
  assert.equal(removal.stored["chatSmokeTask_"+privateId].finishing,false,
    "a failed close-only retry must clear finishing so the next retry can run");
  assert.equal(removal.results.length,1,"failed retry never resends receipt");
  removal.setRemoveFails(false);
  removal.setCloseEligible(privateId,false);
  removal.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<50;i++)await new Promise(r=>setImmediate(r));
  assert.equal(removal.tabs.has(300),true,
    "revoked server receipt must prevent close-only retry");
  assert.equal(removal.results.length,1,
    "revoked receipt must never trigger a second completion submission");
  removal.setCloseEligible(privateId,true);
  removal.setCloseUrl(privateId,"https://chatgpt.com/c/other-chat");
  removal.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<50;i++)await new Promise(r=>setImmediate(r));
  assert.equal(removal.tabs.has(300),true,
    "receipt for another chat URL cannot authorize closing this tab");
  removal.setCloseUrl(privateId,"https://chatgpt.com/c/abcde");
  removal.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  assert.equal(removal.tabs.has(300),false,
    "the accepted tab can be safely closed once removal works");
  assert.equal(removal.results.length,1,
    "closure retry must not retransmit the server result");
  assert.equal(removal.local["chatSmokeOwned_"+privateId],undefined);

  const raced=await simulate({count:1,completion:true,removeFails:true,
    emitRemovedDuringRemove:true});
  await send(raced,{type:"chat-smoke-stage",id:privateId,
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  raced.tabs.get(300).url="https://chatgpt.com/c/abcde";
  raced.ackById.set(privateId,{status:"REPORT_ACKED",job_id:privateId,
    trust_level:"advisory_artifact_integrity_only",ack_id:"f".repeat(32)});
  raced.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  assert.equal(raced.results.length,1,"server ACK accepted exactly once");
  assert.equal(raced.stored["chatSmokeTask_"+privateId].receiptAccepted,true);
  raced.setRemoveFails(false);
  raced.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<65;i++)await new Promise(r=>setImmediate(r));
  assert.equal(raced.tabs.has(300),false,"retry removes only its own tab");
  assert.equal(raced.results.length,1,"retry never resends the DONE receipt");
  assert.equal(raced.logs.filter(x=>x.type==="event"&&
    x.stage==="tab_closed"&&x.reason==="unexpected_close").length,0,
    "onRemoved must not misclassify an ACK-confirmed retry as unexpected");
  assert.equal(raced.stored["chatSmokeTask_"+privateId],undefined,
    "successful retry must not resurrect an orphaned session task");
  assert.equal(raced.local["chatSmokeOwned_"+privateId],undefined,
    "successful retry clears durable ownership");

  const failures=await simulate({count:6});
  const failedFirst=await send(failures,{type:"chat-smoke-finished",id:idAt(0),
    status:"unknown",reason:"send_button_missing",url:""},300);
  assert.equal(failedFirst.reported,"unknown");
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  const failedSecond=await send(failures,{type:"chat-smoke-finished",id:idAt(1),
    status:"unknown",reason:"send_button_missing",url:""},301);
  assert.equal(failedSecond.reported,"unknown");
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  assert.equal(failures.stored.chatSmokeCircuit.paused,false,"transient UI failures use cooldown, not permanent pause");
  assert.ok(failures.stored.chatSmokeCircuit.cooldownUntil>1000000,
    "two pre-send failures schedule bounded cooldown");
  assert.equal(failures.stored.chatSmokeCircuit.failures,2);
  const pendingWhilePaused=failures.queue.length;
  failures.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  assert.equal(failures.queue.length,pendingWhilePaused,"paused state blocks new claims");
  const resumed=await new Promise(resolve=>{
    failures.events.message({type:"chat-smoke-resume"},{},resolve);
  });
  assert.equal(resumed.ok,true);
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  assert.equal(failures.stored.chatSmokeCircuit.paused,false);
  assert.equal(failures.queue.length,pendingWhilePaused-1,
    "explicit popup resume restores single free slot");
  // Transient UI failures cool down and admit exactly ONE fresh probe;
  // old jobs are never requeued, and a saved /c/ message restores throughput.
  const auto=await simulate({count:7,completion:true,realCap:true});
  for(let i=0;i<2;i++){
    const result=await send(auto,{type:"chat-smoke-finished",id:idAt(i),
      status:"unknown",reason:"send_button_missing",url:""},300+i);
    assert.equal(result.reported,"pre_send_failure_requires_reconciliation");
  }
  assert.equal(auto.stored.chatSmokeCircuit.paused,false);
  assert.equal(auto.stored.chatSmokeCircuit.failures,2);
  assert.equal(auto.stored["chatSmokeTask_"+idAt(0)].needsReconciliation,true);
  assert.equal(auto.stored["chatSmokeTask_"+idAt(1)].preSendFailure,true);
  const queuedAtCooldown=auto.queue.length;
  auto.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<35;i++)await new Promise(r=>setImmediate(r));
  assert.equal(auto.queue.length,queuedAtCooldown,"cooldown blocks new claims");
  auto.advance(90001);
  auto.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<60;i++)await new Promise(r=>setImmediate(r));
  assert.equal(auto.queue.length,queuedAtCooldown-1,
    "only one new probe allowed after cooldown");
  const probe=idAt(4);
  assert.equal(auto.stored.chatSmokeCircuit.probeId,probe);
  auto.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<35;i++)await new Promise(r=>setImmediate(r));
  assert.equal(auto.queue.length,queuedAtCooldown-1,
    "unconfirmed probe suppresses further fanout");
  const probeTab=auto.stored["chatSmokeTask_"+probe].tabId;
  await send(auto,{type:"chat-smoke-stage",id:probe,stage:"message_saved",
    url:"https://chatgpt.com/c/verified-probe"},probeTab);
  assert.equal(auto.stored.chatSmokeCircuit.failures,0,
    "only an actual saved message resets soft circuit");
  assert.equal(auto.stored.chatSmokeCircuit.probeId,null);
  // Two failed probes exhaust recovery; do not loop over fresh jobs forever.
  const exhausted=await simulate({count:9,completion:true,realCap:true});
  for(let i=0;i<2;i++)await send(exhausted,{
    type:"chat-smoke-finished",id:idAt(i),
    status:"unknown",reason:"send_button_missing",url:""},300+i);
  exhausted.advance(90001);
  exhausted.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<50;i++)await new Promise(r=>setImmediate(r));
  const probeOne=exhausted.stored.chatSmokeCircuit.probeId;
  const oneTab=exhausted.stored["chatSmokeTask_"+probeOne].tabId;
  await send(exhausted,{type:"chat-smoke-finished",id:probeOne,
    status:"unknown",reason:"send_button_missing",url:""},oneTab);
  assert.equal(exhausted.stored.chatSmokeCircuit.probeFailures,1);
  exhausted.advance(180001);
  exhausted.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<50;i++)await new Promise(r=>setImmediate(r));
  const probeTwo=exhausted.stored.chatSmokeCircuit.probeId;
  assert.ok(probeTwo&&probeTwo!==probeOne);
  const twoTab=exhausted.stored["chatSmokeTask_"+probeTwo].tabId;
  await send(exhausted,{type:"chat-smoke-finished",id:probeTwo,
    status:"unknown",reason:"send_button_missing",url:""},twoTab);
  assert.equal(exhausted.stored.chatSmokeCircuit.paused,true);
  assert.equal(exhausted.stored.chatSmokeCircuit.stopReason,"ui_probe_exhausted");
  const stillQueued=exhausted.queue.length;
  exhausted.advance(600000);
  exhausted.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<35;i++)await new Promise(r=>setImmediate(r));
  assert.equal(exhausted.queue.length,stillQueued,
    "exhausted UI probes remain fail-closed until operator action");
  const research=await simulate({count:1,longPrompt:true});
  const researchTask=await send(research,{type:"chat-smoke-get",id:idAt(0)},300);
  assert.equal(researchTask.minHoldSeconds,180,
    "long agent task defaults to longer observation instead of 5s smoke timer");
  const auth=await simulate({count:4});
  const changed=await send(auth,{type:"chat-smoke-finished",id:idAt(0),
    status:"unknown",reason:"account_context_changed",
    url:"https://chatgpt.com/c/abc123"},300);
  assert.equal(changed.reported,"unknown");
  for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));
  assert.equal(auth.local.chatSmokeAuthPaused,true,
    "account mismatch persists across service-worker/browser restart");
  assert.equal(auth.tabs.has(300),true,"account failure keeps the tab for manual correction");
  assert.equal(auth.logs.some(e=>e.type==="closed"&&e.id===300),false);
  const awaiting=auth.queue.length;
  auth.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));
  assert.equal(auth.queue.length,awaiting,"no new claims while account mismatch unresolved");
  const recovered=await new Promise(resolve=>auth.events.message({
    type:"chat-smoke-resume"
  },{},resolve));
  assert.equal(recovered.ok,true);
  assert.equal(auth.local.chatSmokeAuthPaused,false);
  // A pause must block NEW claims but still let already-running tasks
  // finish after receiving their own immutable, job-bound report ACK.
  const pausedAck=await simulate({count:3,completion:true});
  const saved=await send(pausedAck,{type:"chat-smoke-stage",id:idAt(0),
    stage:"message_saved",url:"https://chatgpt.com/c/abcde"},300);
  assert.equal(saved.ok,true);
  pausedAck.tabs.get(300).url="https://chatgpt.com/c/abcde";
  const authPause=await send(pausedAck,{type:"chat-smoke-finished",id:idAt(1),
    status:"unknown",reason:"account_context_changed",url:""},301);
  assert.equal(authPause.reported,"awaiting_report_ack");
  assert.equal(pausedAck.local.chatSmokeAuthPaused,true);
  const queuedDuringPause=pausedAck.queue.length;
  pausedAck.ackById.set(idAt(0),{status:"REPORT_ACKED",
    job_id:idAt(0),trust_level:"advisory_artifact_integrity_only",
    ack_id:"b".repeat(32)});
  pausedAck.events.alarm({name:"chat-smoke-queue"});
  for(let j=0;j<65;j++)await new Promise(r=>setImmediate(r));
  assert.equal(pausedAck.tabs.has(300),false,
    "verified existing report must be accepted during circuit pause");
  assert.equal(pausedAck.tabs.has(301),true,
    "account-conflict tab must remain preserved during pause");
  assert.equal(pausedAck.queue.length,queuedDuringPause,
    "paused circuit must not claim another job");
  const closed=await simulate({windows:0});
  assert.equal(closed.tabs.size,0,"never opens browser window without existing one");
  const capped=await simulate({count:12});
  assert.equal(capped.tabs.size,2);
  assert.equal(capped.queue.length,10,"hard cap 2");
  // Production cap: 25 agents; only four browser tabs admitted per poll.
  const scaled=await simulate({count:30,realCap:true});
  assert.equal(scaled.tabs.size,4,"initial poll admits four, not 25 at once");
  assert.equal(scaled.queue.length,26);
  for(let i=0;i<6;i++){
    scaled.events.alarm({name:"chat-smoke-queue"});
    for(let j=0;j<65;j++)await new Promise(r=>setImmediate(r));
  }
  assert.equal(scaled.tabs.size,25,"25 active tabs after repeated polls");
  assert.equal(scaled.queue.length,5,"five jobs remain queued at capacity");
  assert.equal(scaled.logs.filter(x=>x.type==="opened").length,25,
    "no duplicate tabs across alarms");
  const outcome=await send(scaled,{type:"chat-smoke-finished",id:idAt(0),
    status:"submitted",userSeen:true,reason:"submitted_after_min_hold",
    url:"https://chatgpt.com/c/abc123"},300);
  assert.equal(outcome.reported,"submitted");
  for(let j=0;j<65;j++)await new Promise(r=>setImmediate(r));
  assert.equal(scaled.tabs.size,25,"released slot refilled without exceeding cap");
  assert.equal(scaled.queue.length,4);
  console.log("PASS: 25-tab cap, four-per-poll ramp, refill, isolation, pause and ACK guards");
})().catch(e=>{console.error(e);process.exitCode=1});
