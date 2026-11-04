/* ChatGPT New Chat Smoke: bounded fanout in the current browser window.
 * One task => one inactive tab. No account API, no new windows, no automatic spawn.
 * Per-task chrome.storage.session keys avoid concurrent map lost updates.
 */
"use strict";
const ALARM="chat-smoke-queue";
const ENDPOINT="http://127.0.0.1:18763";
const PREFIX="chatSmokeTask_";
const OWNED_PREFIX="chatSmokeOwned_";
const OWNED_SCHEMA=1;
const SAVED_CHAT=/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+(?:\?.*)?$/;
const JOB_ID=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
// Only persist canonical conversation paths; exclude query strings/fragments.
function canonicalChatUrl(url){
  const match=typeof url==="string"&&url.match(/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+/);
  return match&&SAVED_CHAT.test(url)?match[0]:"";
}
const CAP=25;
// Limit browser hydration spikes and MV3 service-worker poll duration.
const CLAIMS_PER_POLL=4;
const UI_COOLDOWN_MS=90000;
const MAX_UI_PROBE_FAILURES=2;
let runningPoll=null;
let circuitMutation=Promise.resolve();
// Serialize state transitions across concurrent completion callbacks.
function updateCircuit(change){
  const action=circuitMutation.then(async()=>{
    const data=await chrome.storage.session.get("chatSmokeCircuit");
    const old=data.chatSmokeCircuit||{failures:0,paused:false};
    const next=await change(old);
    if(next)await chrome.storage.session.set({chatSmokeCircuit:next});
  });
  circuitMutation=action.catch(()=>{});
  return action;
}
function recordOutcome(state,reason,jobId){
  return updateCircuit(async old=>{
    const authMismatch=state==="unknown"&&reason==="account_context_changed";
    const preSend=state==="unknown"&&
      ["send_button_missing","editor_not_found","editor_fill_failed"].includes(reason);
    const probing=!!jobId&&old.probeId===jobId;
    let next={...old};
    if(authMismatch){
      // Explicit account errors always require a real popup/user action.
      await chrome.storage.local.set({chatSmokeAuthPaused:true});
      next={...next,paused:true,stopReason:"account_context_changed"};
    }else if(preSend){
      const failures=(old.failures||0)+1;
      const probeFailures=(old.probeFailures||0)+(probing?1:0);
      const exhausted=probeFailures>=MAX_UI_PROBE_FAILURES;
      next={...next,failures,probeFailures,
        probeId:probing?null:old.probeId||null,
        cooldownUntil:exhausted?0:(failures>=2?
          Date.now()+UI_COOLDOWN_MS*(probeFailures+1):0),
        paused:old.paused===true||exhausted,
        stopReason:exhausted?"ui_probe_exhausted":"ui_pre_send_unavailable"};
    }else if(probing&&state==="unknown"){
      // A clicked-but-unconfirmed probe is not a retryable pre-send failure.
      next={...next,paused:true,probeId:null,stopReason:"ambiguous_probe_outcome"};
    }else if((state==="done"||state==="submitted")&&!old.paused&&
      (!old.probeId||probing)){
      next={failures:0,paused:false,probeFailures:0,cooldownUntil:0,probeId:null};
    }
    if(next.paused||(next.cooldownUntil||0)>Date.now()){
      await chrome.storage.local.set({chatSmokeLast:{
        stage:next.paused?"paused_after_failures":"ui_cooldown",
        updatedAt:Date.now()
      }});
    }
    return next;
  });
}
function markSavedSend(jobId){
  // Only a persisted user message at a unique /c/ route proves UI recovery.
  return updateCircuit(old=>{
    if(old.paused||old.probeId&&old.probeId!==jobId)return null;
    if(!old.failures&&!old.probeId)return null;
    return {failures:0,paused:false,probeFailures:0,cooldownUntil:0,probeId:null};
  });
}
async function pausedCircuit(){
  const [session,local]=await Promise.all([
    chrome.storage.session.get("chatSmokeCircuit"),
    chrome.storage.local.get("chatSmokeAuthPaused")
  ]);
  const state=session.chatSmokeCircuit||{};
  return local.chatSmokeAuthPaused===true||state.paused===true||
    !!state.probeId||(state.cooldownUntil||0)>Date.now();
}
async function claimBudget(){
  const {chatSmokeCircuit:state}=await chrome.storage.session.get("chatSmokeCircuit");
  // After a cooldown, probe only ONE fresh campaign job. Never resend a
  // previously failed/ambiguous job, and never fan out during a UI incident.
  return state?.failures>=2?1:CLAIMS_PER_POLL;
}
async function trackProbeClaim(jobId){
  let permitted=true;
  await updateCircuit(old=>{
    if(old.paused||old.probeId||(old.cooldownUntil||0)>Date.now()){
      permitted=false;return null;
    }
    if(old.failures>=2){
      return {...old,probeId:jobId,probeStartedAt:Date.now()};
    }
    return null;
  });
  // The persistent authentication latch is not controlled by UI cooldown.
  const local=await chrome.storage.local.get("chatSmokeAuthPaused");
  return permitted&&local.chatSmokeAuthPaused!==true;
}
async function settings(){
  const res=await fetch(chrome.runtime.getURL("bridge-config.json"),{cache:"no-store"});
  if(!res.ok)throw Error("bridge_config_missing");
  const config=await res.json();
  if(config.endpoint!==ENDPOINT||typeof config.key!=="string"||config.key.length<32)
    throw Error("invalid_bridge_config");
  return config;
}
async function bridge(path,opts={}){
  const config=await settings();
  const response=await fetch(ENDPOINT+path,{
    method:opts.method||"GET",
    headers:{"X-Chat-Smoke-Key":config.key,"Content-Type":"application/json"},
    body:opts.body?JSON.stringify(opts.body):undefined,cache:"no-store"
  });
  if(!response.ok)throw Error("bridge_http_"+response.status);
  return response.json();
}
async function telemetry(job,stage,reason=""){
  if(!job?.id)return;
  try{await bridge("/v1/event",{method:"POST",body:{id:job.id,stage,reason}});}
  catch(e){console.warn("Chat smoke diagnostic:",String(e));}
}
// Executed in a ChatGPT tab via chrome.scripting after extension reload.
// Returns only a unique job UUID, never prompt content or account identifiers.
function identifyOwnedAgentTab(expectedId=null,expectedSavedUrl=null){
  if(!/^https:\/\/chatgpt\.com\//.test(location.href))return null;
  const ids=new Set();
  const messages=document.querySelectorAll(
    '[data-message-author-role="user"],[data-testid="user-message"],[data-message-author-role="human"]');
  for(const message of messages){
    const body=String(message.textContent||"").replace(/\s+/g," ").trim();
    if(!body.includes("Ты независимый исследователь Completion-Gated Runtime.")||
       !body.includes("Local Workspace MCP: list_workspaces alias projects."))continue;
    const matches=[...body.matchAll(/Job ID: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/g)];
    if(matches.length!==1)continue;
    const id=matches[0][1];
    if(body.includes("agent-reports/completion-"+id+".md"))ids.add(id);
  }
  if(ids.size===1)return {id:[...ids][0],proof:"user-message-marker"};
  if(ids.size>1)return null;
  // v0.6.4 live trials demonstrated that user-message attributes can be
  // absent although the content script independently observed the saved
  // prompt. This fallback is allowed ONLY with a previous extension-owned,
  // exact canonical conversation binding. Never use it for arbitrary DOM-only
  // orphan recovery or a copied prompt in a different conversation.
  const saved=/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+$/;
  if(typeof expectedId!=="string"||
     !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(expectedId)||
     typeof expectedSavedUrl!=="string"||!saved.test(expectedSavedUrl)||
     location.origin+location.pathname!==expectedSavedUrl)return null;
  const content=String(document.body?.innerText||"").replace(/\s+/g," ").trim();
  const prefix="Ты независимый исследователь Completion-Gated Runtime.";
  if(!content.includes(prefix))return null;
  const observedIds=new Set(
    [...content.matchAll(/Job ID: ([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/g)]
      .map(m=>m[1]));
  if(observedIds.size>1||(observedIds.size===1&&!observedIds.has(expectedId)))
    return null;
  // Still require that the user prompt is no longer sitting unsent in the
  // editor. Previously recorded message_saved binds this exact URL and job.
  const composer=document.querySelector(
    '[data-testid="prompt-textarea"],#prompt-textarea,[contenteditable="true"],textarea');
  const composerText=String(composer?.value??composer?.textContent??"")
    .replace(/\s+/g," ").trim();
  if(composerText.includes(prefix))return null;
  return {id:expectedId,proof:"bounded-saved-url-visible-prompt"};
}
function validOwnedRecord(record,id){
  return record&&record.schema===OWNED_SCHEMA&&record.id===id&&
    JOB_ID.test(id)&&Number.isInteger(record.tabId)&&record.tabId>=0&&
    Number.isFinite(record.started)&&record.started>=0&&
    (record.savedUrl===""||SAVED_CHAT.test(record.savedUrl))&&
    typeof record.needsReconciliation==="boolean";
}
// Own-only metadata; no prompt, account, cookie, bearer token or chat text.
// One local key per job avoids lost updates between concurrently executing tabs.
async function persistOwned(task){
  if(!task?.completionMode||!Number.isInteger(task.tabId)||!JOB_ID.test(task.id))return;
  const record={schema:OWNED_SCHEMA,id:task.id,tabId:task.tabId,
    started:Number.isFinite(task.started)?task.started:Date.now(),
    savedUrl:canonicalChatUrl(task.savedUrl),
    needsReconciliation:task.needsReconciliation===true};
  await chrome.storage.local.set({[OWNED_PREFIX+task.id]:record});
}
async function storedOwnedRecords(){
  const all=await chrome.storage.local.get(null);
  const result=new Map();
  for(const [key,value] of Object.entries(all)){
    if(!key.startsWith(OWNED_PREFIX))continue;
    const id=key.slice(OWNED_PREFIX.length);
    result.set(id,validOwnedRecord(value,id)?value:null);
  }
  return result;
}
async function identifyTab(tab,expectedId=null,expectedSavedUrl=null){
  if(!Number.isInteger(tab?.id)||tab.discarded===true||
     !SAVED_CHAT.test(tab?.url||"")||!chrome.scripting?.executeScript)return null;
  if(expectedId&&canonicalChatUrl(tab.url)!==expectedSavedUrl)return null;
  try{
    const result=await chrome.scripting.executeScript({
      target:{tabId:tab.id},func:identifyOwnedAgentTab,
      args:[expectedId,expectedSavedUrl]});
    const id=result?.[0]?.result?.id;
    return typeof id==="string"&&JOB_ID.test(id)?id:null;
  }catch(_){return null;}
}
// A stale tab ID or saved URL is NEVER sufficient to authorize receipt/closure.
// Verify same tab, exact route, DOM ownership and no duplicate owned-chat copies.
async function verifiedOwnedTab(task){
  const savedUrl=canonicalChatUrl(task?.savedUrl);
  if(!task?.completionMode||!Number.isInteger(task.tabId)||
     !savedUrl||!chrome.tabs?.query)return false;
  let current;
  try{current=await chrome.tabs.get(task.tabId);}
  catch(_){return false;}
  if(!current||current.discarded===true||
     canonicalChatUrl(current.url)!==savedUrl)return false;
  if(await identifyTab(current,task.id,savedUrl)!==task.id)return false;
  try{
    const tabs=await chrome.tabs.query({});
    for(const tab of tabs){
      if(tab.id===task.tabId||!SAVED_CHAT.test(tab.url||""))continue;
      if(await identifyTab(tab)===task.id)return false;
      if(canonicalChatUrl(tab.url)===savedUrl &&
         await identifyTab(tab,task.id,savedUrl)===task.id)return false;
    }
    return true;
  }catch(_){return false;}
}
async function recoverOrphanedTabs(){
  const scan={tabs_total:0,chat_tabs:0,other_chatgpt_tabs:0,
    non_chatgpt_tabs:0,unknown_url_tabs:0,discarded:0,attempted:0,
    identified:0,unsaved_identified:0,eligible:0,recovered:0,
    ambiguous:0,injection_failed:0,stored_owned:0,stored_invalid:0,
    stored_conflict:0,restored_from_local:0,recovered_dom_only:0};
  try{
    if(!chrome.tabs?.query)return;
    const tabs=await chrome.tabs.query({});
    scan.tabs_total=tabs.length;
    const original=await listActive();
    const already=new Set(original.map(j=>j.id));
    const owned=await storedOwnedRecords();
    for(const record of owned.values()){
      if(record)scan.stored_owned++;else scan.stored_invalid++;
    }
    const candidates=new Map();
    for(const tab of tabs){
      const url=typeof tab.url==="string"?tab.url:"";
      if(!url){scan.unknown_url_tabs++;continue;}
      if(!/^https:\/\/chatgpt\.com\//.test(url)){
        scan.non_chatgpt_tabs++;continue;
      }
      const saved=/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+(?:\?.*)?$/.test(url);
      if(saved)scan.chat_tabs++;else scan.other_chatgpt_tabs++;
      if(tab.discarded===true){scan.discarded++;continue;}
      if(!chrome.scripting?.executeScript)continue;
      scan.attempted++;
      try{
        const matchingOwned=[...owned.values()].filter(v=>v&&
          v.savedUrl&&v.savedUrl===canonicalChatUrl(tab.url));
        const trusted=matchingOwned.length===1?matchingOwned[0]:null;
        // Never guess ownership when multiple persisted IDs point to one URL.
        if(matchingOwned.length>1){scan.ambiguous++;continue;}
        const result=await chrome.scripting.executeScript({
          target:{tabId:tab.id},func:identifyOwnedAgentTab,
          args:[trusted?.id||null,trusted?.savedUrl||null]
        });
        const id=result?.[0]?.result?.id;
        if(typeof id!=="string"||
           !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id))continue;
        scan.identified++;
        // We may diagnose original agent pages at /, but only an exact
        // persisted /c/ URL can be used in a final browser-close receipt.
        if(!saved){scan.unsaved_identified++;continue;}
        if(!Number.isInteger(tab.id))continue;
        if(!candidates.has(id))candidates.set(id,[]);
        candidates.get(id).push(tab);
      }catch(_){scan.injection_failed++;}
    }
    for(const [id,found] of candidates){
      if(found.length!==1){scan.ambiguous++;continue;}
      if(already.has(id))continue;
      const expected=owned.get(id);
      if(owned.has(id)&&expected===null){
        scan.ambiguous++;scan.stored_conflict++;continue;
      }
      // A previously owned tab cannot silently change its conversation ID.
      // Browser restarts may change tab IDs, but the saved URL must match.
      if(expected&&(expected.needsReconciliation||
         (expected.savedUrl&&expected.savedUrl!==canonicalChatUrl(found[0].url)))){
        scan.ambiguous++;scan.stored_conflict++;continue;
      }
      try{
        const eligible=await bridge("/v1/recover?id="+encodeURIComponent(id));
        let receiptAccepted=false;
        if(eligible?.eligible!==true){
          // Once the server has atomically recorded DONE, an MV3 reload must
          // not send /v1/result again. Only a previously stored exact binding
          // plus independent persisted report integrity permits close-only
          // recovery of a tab left open by a failed browser remove.
          if(!expected?.savedUrl)continue;
          const closure=await bridge("/v1/close-eligible?id="+encodeURIComponent(id));
          if(closure?.eligible!==true||
             closure.url!==expected.savedUrl)continue;
          receiptAccepted=true;
        }
        scan.eligible++;
        const tab=found[0];
        await putTask({id,tabId:tab.id,savedUrl:canonicalChatUrl(tab.url),
          started:expected?.started||Date.now(),completionMode:true,
          recovered:true,receiptAccepted,needsReconciliation:false});
        scan.recovered++;
        if(expected){
          scan.restored_from_local++;
          await telemetry({id},"tab_restored");
        }else{
          scan.recovered_dom_only++;
          await telemetry({id},"tab_recovered");
        }
      }catch(_){/* A disputed tab is never closed or replayed. */}
    }
  }finally{
    // Count-only diagnostic. No URLs, prompt text, profile, account or token.
    try{await bridge("/v1/recovery-scan",{method:"POST",body:scan});}
    catch(_){/* Recovery remains fail-closed if diagnostics are offline. */}
  }
}
async function listActive(){
  const values=await chrome.storage.session.get(null);
  return Object.entries(values).filter(([key,v])=>
    key.startsWith(PREFIX)&&v&&typeof v.id==="string").map(([,v])=>v);
}
async function getTask(id){
  const key=PREFIX+id;
  const data=await chrome.storage.session.get(key);
  return data[key]||null;
}
async function putTask(task){
  if(task?.completionMode&&Number.isInteger(task.tabId))
    await persistOwned(task);
  await chrome.storage.session.set({[PREFIX+task.id]:task});
}
async function dropTask(id){
  await chrome.storage.session.remove(PREFIX+id);
  await chrome.storage.local.remove(OWNED_PREFIX+id);
}
async function finish(job,status,url="",responseText="",options={}){
  if(!job||job.finishing)return;
  // Finalize a completion report only for the exact uniquely identified agent
  // conversation. A reused tab ID or navigated user tab must never be closed.
  if(job.completionMode){
    if(!await verifiedOwnedTab(job)){
      if(!job.bindingWarningNoted){
        await telemetry(job,"tab_identity_unverified");
        await putTask({...job,bindingWarningNoted:true});
      }
      return;
    }
  }
  await putTask({...job,finishing:true});
  let accepted=false;
  try{
    await bridge("/v1/result",{method:"POST",body:{
      id:job.id,status:status,
      url:canonicalChatUrl(url)||"",
      response_text:responseText,
      ack_id:options.ackId||""
    }});
    accepted=true;
    await telemetry(job,"receipt_accepted",status);
  }catch(e){
    await telemetry(job,"receipt_failed","receipt_rejected");
    console.warn("Chat smoke receipt uncertain:",String(e));
  }
  if(job.completionMode && !accepted){
    await putTask({...job,finishing:false,needsReconciliation:true});
    await telemetry(job,"report_disputed","receipt_rejected");
    return;
  }
  let closed=false;
  if(Number.isInteger(job.tabId)&&options.preserveTab===true){
    await telemetry(job,"tab_preserved","account_context_changed");
  } else if(Number.isInteger(job.tabId)){
    try{
      // Obtain a fresh route snapshot directly before the destructive action.
      // Browser APIs cannot make a URL check + remove atomic, so this remains
      // fail-closed on any changed route, mismatch, or unavailable tab.
      if(job.completionMode && !await verifiedOwnedTab(job)){
        await telemetry(job,"tab_close_skipped");
      }else{
        const tab=await chrome.tabs.get(job.tabId);
        const eligible=tab&&typeof tab.url==="string"&&
          (job.completionMode?
            canonicalChatUrl(tab.url)===canonicalChatUrl(job.savedUrl):
            tab.url.startsWith("https://chatgpt.com/"));
        if(eligible){
          await chrome.tabs.remove(job.tabId);
          closed=true;
          await telemetry(job,"tab_closed");
        }else{
          await telemetry(job,"tab_close_skipped");
        }
      }
    }catch(e){await telemetry(job,"tab_close_failed","browser_tab_unavailable");}
  }
  if(job.completionMode&&accepted&&!closed){
    // Server DONE is durable, but browser closure was not. Preserve ownership
    // evidence; never pretend the tab closed or resend a completion receipt.
    await putTask({...job,finishing:false,receiptAccepted:true});
    return;
  }
  await dropTask(job.id);
  if(accepted)void poll();
}
async function retryAcceptedClosure(task){
  if(!task?.completionMode||task.receiptAccepted!==true||
     !Number.isInteger(task.tabId))return;
  // A cached receiptAccepted flag is not continuing authorization. An ACK,
  // report or DONE record can have been disputed since the prior failed close.
  // Revalidate the immutable server receipt every retry; unavailable server
  // must preserve the tab without inventing a successful closure.
  let closure;
  try{closure=await bridge("/v1/close-eligible?id="+encodeURIComponent(task.id));}
  catch(_){return;}
  if(closure?.eligible!==true||closure.url!==canonicalChatUrl(task.savedUrl))return;
  try{
    if(!await verifiedOwnedTab(task))return;
    const current=await chrome.tabs.get(task.tabId);
    if(!current||canonicalChatUrl(current.url)!==
       canonicalChatUrl(task.savedUrl))return;
    // Chrome may emit tabs.onRemoved *before* tabs.remove resolves. Mark this
    // intentional close durably in session first, just like finish() does.
    // Otherwise onRemoved mislabels a verified DONE as unexpected_close.
    await putTask({...task,finishing:true});
    await chrome.tabs.remove(task.tabId);
    await telemetry(task,"tab_closed");
    await dropTask(task.id);
  }catch(_){
    // A failed removal must be retryable: do not strand a finishing=true task.
    await putTask({...task,finishing:false,closeRetryNoted:true});
    if(!task.closeRetryNoted){
      await telemetry(task,"tab_close_failed","browser_tab_unavailable");
    }
  }
}
async function dispatch(job,windowId){
  if(!job||typeof job.id!=="string"||typeof job.prompt!=="string")return;
  // Substantive research prompts must not be cut off after the 5-second smoke
  // timeout. This is a temporary safeguard, not the completion-gated ACK.
  const hold=Number.isInteger(job.minHoldSeconds)?Math.max(5,Math.min(180,job.minHoldSeconds)):
    job.prompt.length>=140?180:5;
  const completionMode=job.completion_mode==="report"&&
    job.report_name==="completion-"+job.id+".md";
  const task={id:job.id,prompt:job.prompt,minHoldSeconds:hold,
    completionMode,tabId:null,started:Date.now(),savedUrl:""};
  await putTask(task);
  await telemetry(task,"tab_opening");
  await chrome.storage.local.set({chatSmokeLast:{id:task.id,stage:"tab_opening",
    updatedAt:Date.now()}});
  try{
    const tab=await chrome.tabs.create({
      windowId,url:"https://chatgpt.com/#chat-launch-smoke=bg-"+encodeURIComponent(job.id),
      active:false
    });
    await putTask({...task,tabId:tab.id});
    await telemetry(task,"tab_created");
  }catch(e){
    await telemetry(task,"runtime_error","tab_open_failed");
    await finish(task,"unknown");
  }
}
async function poll(){
  if(runningPoll)return runningPoll;
  runningPoll=(async()=>{
    try{await recoverOrphanedTabs();}
    catch(e){console.warn("Task recovery unavailable:",String(e));}
    const current=await listActive();
    for(const task of current){
      if(!task.finishing&&Date.now()-task.started>420000){
        if(task.completionMode){
          if(!task.watchdogNoted){
            await telemetry(task,"report_watchdog");
            await putTask({...task,watchdogNoted:true});
          }
          // Timeout is only an alert. An unacknowledged report is never
          // interpreted as done and its tab is never auto-closed.
        } else {
          await telemetry(task,"watchdog_timeout");
          await finish(task,"unknown");
        }
      }
    }
    const windows=await chrome.windows.getAll({windowTypes:["normal"]});
    const win=windows.find(x=>Number.isInteger(x.id));
    const paused=await pausedCircuit();
    try{await bridge("/v1/heartbeat",{method:"POST",body:{
      version:"0.6.6",state:paused?"paused":win?"polling":"no_window",
      windows:windows.length
    }});}catch(e){console.warn("Heartbeat unavailable:",String(e));}
    if(!win)return;
    // Pausing prevents new admissions but MUST NOT block report ACKs or
    // closure of already verified jobs. This keeps long-running agents from
    // being stranded when another tab triggers the circuit breaker.
    // Closure is driven by the bridge's scoped immutable report ACK,
    // NOT by model text or the 180-second browser hold.
    const awaiting=await listActive();
    for(const task of awaiting){
      if(task.receiptAccepted===true){
        await retryAcceptedClosure(task);
        continue;
      }
      if(!task.completionMode||task.finishing||task.needsReconciliation||
        !task.savedUrl)continue;
      try{
        const response=await bridge("/v1/ack?id="+encodeURIComponent(task.id));
        if(response?.ack?.status==="REPORT_ACKED"&&
           response.ack.job_id===task.id&&
           response.ack.trust_level==="advisory_artifact_integrity_only"&&
           /^[0-9a-f]{32}$/.test(response.ack.ack_id)){
          const withAck={...task,ackNoted:true};
          if(!task.ackNoted){
            await telemetry(task,"report_acked");
            await putTask(withAck);
          }
          await finish(withAck,"done",task.savedUrl,"",{ackId:response.ack.ack_id});
        }else if(response?.pending&&response.pending!=="report_not_found"){
          await telemetry(task,"report_pending");
        }
      }catch(e){
        console.warn("Report polling unavailable:",String(e));
      }
    }
    // Keep the circuit breaker authoritative for NEW work only.
    if(await pausedCircuit())return;
    const stillActive=await listActive();
    const budget=await claimBudget();
    let capacity=Math.min(budget,Math.max(0,CAP-stillActive.length));
    // The bridge also bounds total claims; do not claim a burst of 25 tabs.
    while(capacity-- > 0){
      if(await pausedCircuit())return;
      let response;
      try{response=await bridge("/v1/next");}
      catch(e){return;}
      if(!response?.job)return;
      // The bridge claim is irreversible. If a concurrent auth/UI pause
      // arrives after /next, quarantine this claimed job rather than sending
      // from a newly disputed account or replaying it later.
      if(!await trackProbeClaim(response.job.id)){
        await telemetry(response.job,"runtime_error","claim_paused_race");
        return;
      }
      await dispatch(response.job,win.id);
      // Spread browser hydration across a few seconds while keeping the
      // resulting chats concurrent. This avoids ten editors racing for focus.
      if(capacity>=0) await new Promise(resolve=>setTimeout(resolve,1200));
    }
  })().finally(()=>{runningPoll=null;});
  return runningPoll;
}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.type==="chat-smoke-resume" && !sender.tab){
    (async()=>{
      await chrome.storage.session.set({
        chatSmokeCircuit:{failures:0,paused:false}
      });
      await chrome.storage.local.set({
        chatSmokeAuthPaused:false,
        chatSmokeLast:{
        stage:"manually_resumed",updatedAt:Date.now()
      }});
      sendResponse({ok:true});
      void poll();
    })().catch(()=>sendResponse({ok:false}));
    return true;
  }
  if(!message||!["chat-smoke-get","chat-smoke-finished","chat-smoke-stage"].includes(message.type))return;
  (async()=>{
    const task=await getTask(message.id);
    const valid=task&&task.id===message.id&&sender.tab&&sender.tab.id===task.tabId&&!task.finishing;
    if(!valid){sendResponse({ok:false});return;}
    if(message.type==="chat-smoke-get"){
      if(task.recovered===true||typeof task.prompt!=="string"){
        sendResponse({ok:false,reason:"already_sent"});return;
      }
      sendResponse({ok:true,prompt:task.prompt,minHoldSeconds:task.minHoldSeconds,
        completionMode:task.completionMode===true});
      return;
    }
    if(message.type==="chat-smoke-stage"){
      const allowed=["assigned","editor_found","prompt_filled","send_clicked",
        "message_saved","handoff_failed","send_ui_diagnostic"];
      if(!allowed.includes(message.stage)){sendResponse({ok:false});return;}
      const diagnosticReasons=["editor_absent","button_absent","button_disabled",
        "button_not_scoped","button_ambiguous"];
      if(message.stage==="send_ui_diagnostic"&&
         !diagnosticReasons.includes(message.reason)){
        sendResponse({ok:false});return;
      }
      await chrome.storage.local.set({chatSmokeLast:{
        id:task.id,stage:message.stage,updatedAt:Date.now()}});
      await telemetry(task,message.stage,
        message.stage==="send_ui_diagnostic"?message.reason:"");
      if(message.stage==="message_saved"&&task.completionMode){
        const candidate=typeof message.url==="string"?message.url:"";
        if(/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+(?:\?.*)?$/.test(candidate)){
          const canonical=canonicalChatUrl(candidate);
          if(task.savedUrl&&canonicalChatUrl(task.savedUrl)!==canonical){
            await putTask({...task,needsReconciliation:true});
            await telemetry(task,"tab_binding_disputed");
          }else if(canonical){
            await putTask({...task,savedUrl:canonical});
            await markSavedSend(task.id);
            void poll();
          }
        }
      }
      sendResponse({ok:true});return;
    }
    const reasons=["unexpected_route","editor_not_found","editor_fill_failed",
      "send_button_missing","assistant_confirmed","assistant_timeout",
      "user_message_not_confirmed","content_script_error","submitted_after_5s",
      "submitted_after_min_hold","account_context_changed"];
    const reason=reasons.includes(message.reason)?message.reason:"unknown";
    const candidate=typeof message.url==="string"?message.url:"";
    const url=/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+(?:\?.*)?$/.test(candidate)?candidate:"";
    const done=message.status==="done"&&!!url&&message.assistantSeen===true;
    const submitted=message.status==="submitted"&&!!url&&message.userSeen===true;
    const result=done?"done":submitted?"submitted":"unknown";
    await chrome.storage.local.set({chatSmokeLast:{
      id:task.id,stage:result==="unknown"?reason:result,updatedAt:Date.now()}});
    await telemetry(task,result,reason);
    await recordOutcome(result,reason,task.id);
    const responseText=result==="done" && typeof message.responseText==="string"?
      message.responseText.slice(0,8000):"";
    if(task.completionMode){
      // A saved user prompt, assistant DOM completion or authentication error
      // cannot establish an independently verified report ACK.
      const preSend=["send_button_missing","editor_not_found","editor_fill_failed"]
        .includes(reason);
      if(reason==="account_context_changed"||preSend){
        await putTask({...task,needsReconciliation:true,
          ...(preSend?{preSendFailure:true}:{})});
        await telemetry(task,"report_disputed",reason);
      }
      // A pre-click failure is NOT a report-ACK wait and is NEVER requeued.
      // Only a checked owner recovery can decide what happens to this job.
      sendResponse({ok:true,reported:preSend?
        "pre_send_failure_requires_reconciliation":"awaiting_report_ack"});return;
    }
    await finish(task,result,url,responseText,{
      preserveTab:reason==="account_context_changed"
    });
    sendResponse({ok:true,reported:result});
  })().catch(e=>{console.warn("Chat smoke message:",String(e));sendResponse({ok:false});});
  return true;
});
chrome.tabs.onRemoved.addListener(tabId=>{
  listActive().then(tasks=>{
    for(const task of tasks){
      if(task.tabId===tabId&&!task.finishing){
        if(task.completionMode){
          // The browser tab has gone but the bridge job may have been sent:
          // never auto-resend or release it as an accepted result.
          return telemetry(task,"tab_closed","unexpected_close")
            .then(()=>putTask({...task,needsReconciliation:true,lostTab:true}));
        }
        return telemetry(task,"tab_closed","unexpected_close")
          .then(()=>finish(task,"unknown"));
      }
    }
  }).catch(()=>{});
});
function register(){
  chrome.alarms.create(ALARM,{periodInMinutes:0.5});
  void poll();
}
chrome.runtime.onInstalled.addListener(register);
chrome.runtime.onStartup.addListener(register);
chrome.alarms.onAlarm.addListener(a=>{if(a.name===ALARM)void poll();});
register();
