/* Hermes transport for the existing Web Companion. Disabled until UI opt-in. */
(()=>{
"use strict";
const ROOT="http://127.0.0.1:18765",ACTIVE="hermesRelayActive_v1",ENABLED="hermesRelayEnabled_v1",WORKER="hermesRelayWorker_v1";

function makeHermesTerminalRecovery(storage,inspect){
 const ACTIVE="hermesRelayActive_v1",PREFIX="hermesRelayTerminal_v1_";
 const snapshot=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==="object"&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
 return async function retire(task){
  if(!task?.job||typeof task.job.id!=="string"||!/^[a-zA-Z0-9-]{1,100}$/.test(task.job.id)||
     typeof task.job.lease!=="string"||!task.job.lease)return {ok:false,reason:"recovery_binding_missing"};
  if(!["uncertain","blocked","intent","saved","launch_ack_unverified"].includes(task.state))
   return {ok:false,reason:"recovery_active_phase"};
  const original=snapshot(task),key=PREFIX+task.job.id;
  const server=await inspect(task);
  if(server?.ok!==true||!["done","blocked","uncertain"].includes(server.state))
   return {ok:false,reason:"recovery_not_terminal"};
  if(server.tab!=null&&server.tab!==task.tabId)return {ok:false,reason:"recovery_tab_mismatch"};
  if(snapshot((await storage.get(ACTIVE))[ACTIVE])!==original)return {ok:false,reason:"recovery_active_changed"};
  const previous=(await storage.get(key))[key];
  if(previous&&(previous.schema!=="hermes-terminal-v1"||snapshot(previous.task)!==original||previous.noReplay!==true))
   return {ok:false,reason:"recovery_archive_conflict"};
  const receipt=previous||{schema:"hermes-terminal-v1",task,server:{state:server.state,tab:server.tab??null},
   noReplay:true,retiredAt:Date.now()};
  await storage.set({[key]:receipt});
  const verified=(await storage.get(key))[key];
  if(snapshot(verified)!==snapshot(receipt))return {ok:false,reason:"recovery_archive_unverified"};
  if(snapshot((await storage.get(ACTIVE))[ACTIVE])!==original)return {ok:false,reason:"recovery_active_changed"};
  await storage.set({[ACTIVE]:null});
  if((await storage.get(ACTIVE))[ACTIVE]!=null)return {ok:false,reason:"recovery_release_unverified"};
  return {ok:true,state:"idle",archived:true,oldAttemptNotReplayed:true};
 };
}

let polling=false;
const finishing=new Set();
let warmPollEpoch=0;
// Four bounded, idle-only checks catch a following Hermes tool turn without
// waiting for the next 30s alarm. They never retry a claimed request or Send.
function wakeWarmPolls(){
 const epoch=++warmPollEpoch;
 for(const delay of [0,1000,3000,7000])setTimeout(()=>{
  void(async()=>{
   if(epoch!==warmPollEpoch||await read())return;
   await poll(); // enabled, ownership, durable fence and server claim still apply
  })().catch(()=>{});
 },delay);
}
 const injecting=new Set();
async function api(path,body){
 const response=await fetch(chrome.runtime.getURL("hermes-relay-config.json"),{cache:"no-store"});
 const cfg=await response.json();
 if(cfg.endpoint!==ROOT||typeof cfg.key!=="string"||cfg.key.length<32)throw Error("relay_config_invalid");
 const r=await fetch(ROOT+path,{method:body===undefined?"GET":"POST",headers:{"Authorization":"Bearer "+cfg.key,"Content-Type":"application/json"},
  body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(5000),cache:"no-store"});
 if(!r.ok)throw Error("relay_request_failed");return r.json();
}
const read=async()=>((await chrome.storage.local.get(ACTIVE))[ACTIVE]||null);
const save=async value=>chrome.storage.local.set({[ACTIVE]:value});
async function event(task,action,extra={}){
 return api("/bridge/event",{id:task.job.id,lease:task.job.lease,tab:task.tabId,action,...extra});
}
const retireTerminal=makeHermesTerminalRecovery(chrome.storage.local,task=>event(task,"inspect"));
async function owned(task,rootOnly=false,backgroundOnly=true){
 if(!Number.isInteger(task?.tabId))return false;
 try{
  const tab=await chrome.tabs.get(task.tabId);
  if(tab.windowId!==task.windowId||(backgroundOnly&&tab.active!==false)||tab.discarded||tab.status!=="complete")return false;
  const url=new URL(tab.url);
  return url.origin==="https://chatgpt.com"&&(rootOnly?url.pathname==="/":
   url.pathname==="/"||/^\/c\/[a-z0-9-]+$/.test(url.pathname));
 }catch(_){return false;}
}

function hermesBootstrapProbe(id){
 const running=globalThis.__HermesRelayContent===true;
 const assigned=running?(globalThis.__HermesRelayContentJobId||globalThis.__HermesRelayAssignedId):
  globalThis.__HermesRelayAssignedId;
 if(assigned&&assigned!==id)return {protocol:2,assignedId:assigned,running,conflict:true};
 if(!running)globalThis.__HermesRelayAssignedId=id;
 return {protocol:2,assignedId:assigned||(!running?id:null),running,conflict:false};
}
function validBootstrapResult(results,id,documentId){
 if(!Array.isArray(results)||results.length!==1)return null;
 const entry=results[0],value=entry?.result;
 if(entry?.frameId!==0||typeof entry.documentId!=="string"||!entry.documentId||
    (documentId&&entry.documentId!==documentId)||value?.protocol!==2||
    value.assignedId!==id||value.conflict===true)return null;
 return {documentId:entry.documentId,running:value.running===true};
}
async function inject(task){
 if(injecting.has(task.job.id)||!await owned(task,true))return;
 injecting.add(task.job.id);
 let reason="content_start_unverified";
 try{
  let current=await read();
  if(current?.job.id!==task.job.id||current.job.lease!==task.job.lease||current.state!=="created")return;
  if((await event(current,"inspect")).state!=="claimed")return;
  current={...current,state:"bootstrapping",bootstrapAt:Date.now(),bootstrapProtocol:2,bootstrapFileAttempts:0};
  await save(current);
  let binding=null;
  for(let attempt=0;attempt<2&&!binding;attempt++){
   if(!await owned(current,true)){reason="content_tab_not_ready";break;}
   if((await event(current,"inspect")).state!=="claimed")return;
   try{
    const result=await chrome.scripting.executeScript({target:{tabId:task.tabId,frameIds:[0]},
     func:hermesBootstrapProbe,args:[task.job.id]});
    binding=validBootstrapResult(result,task.job.id);
    if(!binding){reason="content_document_binding_unverified";break;}
   }catch(_){
    reason="content_probe_failed";
    if(attempt===0)await new Promise(resolve=>setTimeout(resolve,250));
   }
  }
  if(!binding)throw Error(reason);
  // Never re-run a runner already bound to this exact attempt and document.
  if(binding.running){
   const fresh=await read();
   if(fresh?.job.id===task.job.id&&fresh.job.lease===task.job.lease&&fresh.state==="bootstrapping")
    await save({...fresh,documentId:binding.documentId});
   return;
  }
  current={...current,documentId:binding.documentId};await save(current);
  for(let attempt=0;attempt<2;attempt++){
   const fresh=await read();
   if(fresh?.job.id!==task.job.id||fresh.job.lease!==task.job.lease||
      fresh.documentId!==binding.documentId||fresh.state!=="bootstrapping")return;
   if(!await owned(fresh,true)){reason="content_tab_not_ready";throw Error(reason);}
   if((await event(fresh,"inspect")).state!=="claimed")return;
   current={...fresh,bootstrapFileAttempts:attempt+1};await save(current);
   let injectionFailed=false;
   try{
    await chrome.scripting.executeScript({target:{tabId:task.tabId,documentIds:[binding.documentId]},
     files:["core.js","hermes_relay_content.js"]});
   }catch(_){injectionFailed=true;}
   // A lost files ACK is checked in the SAME document before any retry.
   let receipt=null;
   try{
    const results=await chrome.scripting.executeScript({target:{tabId:task.tabId,documentIds:[binding.documentId]},
     func:hermesBootstrapProbe,args:[task.job.id]});
    receipt=validBootstrapResult(results,task.job.id,binding.documentId);
   }catch(_){}
   if(receipt?.running)return;
   if(!receipt){reason="content_document_lost";throw Error(reason);}
   // No replay after a Send intent, navigation, foreign runner, or lost probe.
   if((await event(current,"inspect")).state!=="claimed")return;
   reason=injectionFailed?"content_injection_failed":"content_runner_not_started";
   if(attempt===0)await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw Error(reason);
 }catch(_){
  const fresh=await read();
  if(fresh?.job.id===task.job.id&&fresh.job.lease===task.job.lease&&
     ["created","bootstrapping"].includes(fresh.state)){
   try{
    const server=await event(fresh,"inspect");
    if(server.state!=="claimed")return;
    const result=await event(fresh,"blocked",{reason});
    await save({...fresh,state:result.state,reason});
   }catch(_){}
  }
 }finally{injecting.delete(task.job.id);}
}

// Choose a confirmed normal window BEFORE claiming a request. A popup or
// closing window must not consume a queue item. Never guess among multiple
// normal windows if there is no valid last-focused normal window.
async function launchWindow(){
 let win=null;
 try{win=await chrome.windows.getLastFocused();}catch(_){}
 if(win?.type!=="normal"||!Number.isInteger(win.id)){
   let choices=[];
   try{choices=await chrome.windows.getAll({windowTypes:["normal"]});}catch(_){}
   const normal=choices.filter(x=>x?.type==="normal"&&Number.isInteger(x.id));
   if(normal.length!==1)return null;
   win=normal[0];
 }
 // Re-check before claim; a stale window is not a valid tab ownership target.
 try{
   const fresh=await chrome.windows.get(win.id);
   return fresh?.id===win.id&&fresh.type==="normal"?fresh:null;
 }catch(_){return null;}
}
const LAUNCH_FAILURE={
  window:"window_preflight_failed",
  worker:"worker_state_failed",
  claim:"relay_claim_unverified",
  fence:"launch_fence_unverified",
  tab_create:"tab_create_unverified",
  tab_save:"tab_binding_persist_failed",
  tab_validate:"tab_binding_unverified",
  inject:"content_injection_unverified"
};
async function poll(){
 if(polling||finishing.size)return;polling=true;
 let phase="window",claimedId=null,pendingTask=null;
 try{
  if(!(await chrome.storage.local.get(ENABLED))[ENABLED])return;
  const active=await read();
  if(active){
   if(["uncertain","blocked","intent","saved","launch_ack_unverified"].includes(active.state)){
    let retired=null;
    try{retired=await retireTerminal(active);}catch(_){}
    if(retired?.ok!==true||await read())return;
    // The old attempt is durably archived as noReplay before a new claim.
   }else{
    if(active.state==="created")await inject(active);
    else if(active.state==="bootstrapping"&&Date.now()-active.bootstrapAt>30000){
     const r=await event(active,"blocked",{reason:"content_handoff_timeout"});
     await save({...active,state:r.state,reason:"content_handoff_timeout"});
    }
    return;
   }
  }
  const win=await launchWindow();if(!win)return;
  phase="worker";
  let worker=(await chrome.storage.local.get(WORKER))[WORKER];
  if(!worker){worker=crypto.randomUUID();await chrome.storage.local.set({[WORKER]:worker});}
  phase="claim";
  const reply=await api("/bridge/claim",{worker});if(!reply.job)return;
  claimedId=reply.job.id;
  if((await chrome.storage.local.get("hermesRelayTerminal_v1_"+claimedId))["hermesRelayTerminal_v1_"+claimedId]){
   try{await event({job:reply.job,tabId:null},"blocked",{reason:"retired_attempt_not_replayable"});}catch(_){}
   return;
  }
  // Persist the non-replay fence before even trying to create a tab.
  phase="fence";
  let task={job:reply.job,windowId:win.id,tabId:null,state:"creating"};
  pendingTask=task;
  await save(task);
   if(!(await chrome.storage.local.get(ENABLED))[ENABLED]){
    const stopped=await event(task,"blocked",{reason:"user_disabled"});
    await save({...task,state:stopped.state,reason:"user_disabled"});
    return; // Disable may have arrived while the claim ACK was in flight.
   }
  phase="tab_create";
  const tab=await chrome.tabs.create({windowId:win.id,active:false,url:"https://chatgpt.com/#hermes-relay="+task.job.id});
  phase="tab_save";
  task={...task,tabId:tab.id,state:"created"};await save(task);
  phase="tab_validate";
  if(tab.windowId!==win.id||tab.active!==false)throw Error("tab_binding_unverified");
  if(tab.status==="complete"){phase="inject";await inject(task);}
 }catch(_){
  // Never store raw error strings (they can reveal paths, URLs, or prompts).
  const reason=LAUNCH_FAILURE[phase]||"launch_or_transport_unverified";
  let stored=null;
  try{stored=await read();}catch(_){}
  // Claim ownership can still be proven from this same poll call if storing
  // the PRE-CREATE fence itself failed. No tab has been created at this phase.
  const task=stored?.job?.id===claimedId?stored:
   (phase==="fence"&&pendingTask?.job?.id===claimedId?pendingTask:null);
  if(claimedId===null||task?.job?.id!==claimedId||
    !["creating","created"].includes(task.state))return;
  let result=null;
  try{result=await event(task,"blocked",{reason});}catch(_){}
  // A lost ACK is NOT permission to reset or retry the active job.
  if(result?.state==="blocked"||result?.state==="uncertain"){
   try{await save({...task,state:result.state,reason});}catch(_){}
  }else{
   try{await save({...task,state:"launch_ack_unverified",reason});}catch(_){}
  }
 }finally{polling=false;}
}
function controller(sender){
 const url=sender?.url;
 if(sender?.id!==chrome.runtime.id)return false;
 const popup=chrome.runtime.getURL("popup.html"),panel=chrome.runtime.getURL("popup.html?panel=1");
 if(!sender.tab)return url===popup||url===panel;
 return url===panel&&sender.tab.url===panel&&(sender.frameId===undefined||sender.frameId===0);
}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
 if(!m?.type?.startsWith("hermes-relay-"))return false;
 (async()=>{
  if(["hermes-relay-enable","hermes-relay-disable","hermes-relay-status"].includes(m.type)){
   if(!controller(sender))return {ok:false,reason:"controller_required"};
   if(m.type==="hermes-relay-enable"){
    await api("/v1/models");await chrome.storage.local.set({[ENABLED]:true});wakeWarmPolls();
   }
   if(m.type==="hermes-relay-disable"){
    warmPollEpoch++;
    await chrome.storage.local.set({[ENABLED]:false});
    const active=await read();
    if(active){await event(active,"blocked",{reason:"user_disabled"});await save(null);}
   }
   const task=await read();return {ok:true,enabled:!!(await chrome.storage.local.get(ENABLED))[ENABLED],state:task?.state||"idle",reason:task?.reason||"",reasoningWarning:task?.reasoningWarning||"",bootstrapProtocol:2,queueWakeProtocol:1};
  }
  const task=await read();
  if(!task||task.job.id!==m.id||task.tabId!==sender.tab?.id||sender.frameId>0||
      (task.documentId&&sender.documentId!==task.documentId))return {ok:false,reason:"tab_binding_required"};
  if(m.type==="hermes-relay-get"){
   const s=await event(task,"inspect");
   if(s.state!=="claimed"||!await owned(task,true))return {ok:false,reason:"attempt_not_unsent"};
   return {ok:true,prompt:task.job.prompt,minHoldSeconds:5,completionMode:false};
  }
  if(m.type==="hermes-relay-intent"){
   if(!(await chrome.storage.local.get(ENABLED))[ENABLED]||!await owned(task,true))return {ok:false,reason:"send_disabled"};
   const result=await event(task,"intent");await save({...task,state:"intent"});return result;
  }
  if(m.type==="hermes-relay-preflight"){
   if(!(await chrome.storage.local.get(ENABLED))[ENABLED]||!await owned(task,true))return {ok:false};
   return {ok:(await event(task,"inspect")).state==="intent"};
  }
     if(m.type==="hermes-relay-stage"){
     if(m.stage==="reasoning_warning"){
      // Bounded metadata only: no prompt, DOM text, account or chat identity.
      const reason=typeof m.reason==="string"&&/^[a-z_]{1,60}$/.test(m.reason)?
       m.reason:"high_unverified";
      if(!["preparing","intent"].includes(task.state)||!await owned(task,true))
       return {ok:false,reason:"tab_binding_required"};
      await save({...task,reasoningWarning:reason});
      return {ok:true};
     }
     if(m.stage==="assigned"){

     if(!await owned(task,true))return {ok:false,reason:"tab_binding_required"};
     await save({...task,state:"preparing"});return {ok:true};
    }
    if(m.stage==="message_saved"){
    if(!await owned(task,false,false))return {ok:false,reason:"saved_tab_unverified"};
    const tab=await chrome.tabs.get(task.tabId),url=new URL(tab.url);
    const canonical=url.origin+url.pathname;
    if(m.url!==canonical)return {ok:false,reason:"saved_url_mismatch"};
    const result=await event(task,"saved",{url:canonical});await save({...task,state:"saved",savedUrl:canonical});return result;
   }
   return {ok:true};
  }
  if(m.type==="hermes-relay-finished"){
    if(finishing.has(task.job.id))return {ok:false,reason:"result_in_progress"};
    finishing.add(task.job.id);
    try{
   if(m.status!=="done"){
     const result=await event(task,"blocked",{reason:m.reason});
     if(!["blocked","uncertain"].includes(result.state))return result;
     const fresh=await read();
     if(fresh?.job?.id!==task.job.id||fresh.job.lease!==task.job.lease)
      return {ok:false,reason:"completion_state_changed"};
     await save({...fresh,state:result.state,reason:m.reason});return result;
   }
   if(m.userSeen!==true||m.assistantSeen!==true||task.state!=="saved"||task.savedUrl!==m.url||!await owned(task,false,false))
    return {ok:false,reason:"reply_unverified"};
    const result=await event(task,"result",{url:m.url,text:m.responseText});
    if(result.state!=="done")return {ok:false};
    const fresh=await read();
    if(fresh?.job?.id!==task.job.id||fresh.job.lease!==task.job.lease||
       fresh.state!=="saved"||fresh.savedUrl!==task.savedUrl)
     return {ok:false,reason:"completion_state_changed"};
    await save(null); // Only an acknowledged, still-owned result releases the slot.
    if(await owned(task)){
     const tab=await chrome.tabs.get(task.tabId),url=new URL(tab.url);
     if(url.origin+url.pathname===task.savedUrl)try{await chrome.tabs.remove(task.tabId);}catch(_){}
    }
    wakeWarmPolls();
    return {ok:true};
   }finally{finishing.delete(task.job.id);}
  }
  return {ok:false,reason:"unknown_message"};
 })().then(reply,()=>reply({ok:false,reason:"relay_unavailable"}));
 return true;
});
chrome.tabs.onUpdated.addListener((id,change)=>{
 if(change.status==="complete")void(async()=>{const task=await read();if(task?.tabId===id&&task.state==="created")await inject(task);})().catch(()=>{});
});
// Poll at Chromium's 30-second alarm floor to reduce cold-queue latency.
chrome.alarms.create("hermes-relay-poll",{periodInMinutes:0.5});
chrome.alarms.onAlarm.addListener(a=>{if(a.name==="hermes-relay-poll")void poll();});
})();


