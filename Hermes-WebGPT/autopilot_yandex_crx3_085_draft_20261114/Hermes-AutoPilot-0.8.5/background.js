/** Hermes AutoPilot 0.8.5 candidate. Bounded DOM readiness and drafts; Send remains disabled until signed Web Companion integration. */
import {createRun,validatePlan,connectTab,schedule,ownerPause,resumeFromPause,ownerStop,ownerSetConcurrency,ownerApproveTask,ownerRecoverMissingTask,markTabMissing,releaseIdleMissingSlot,summarizeRun,watchdogScan,trustedLivenessScan,ownerAcknowledgeProgress,validateRunSnapshot} from './supervisor.mjs';
import {analyzePlan,queueInsights,auditInsights,workerLimit,capacityAdvice} from './planning.mjs';
import {probeCompanion} from './companion_probe.mjs';
import {readRelayCapacity} from './relay_capacity_probe.mjs';
import {previewTurnBinding} from './handoff_contract.mjs';
import {insertChatDraft} from './draft_dom.mjs';
import {ensureChatReady} from './browser_ready.mjs';
import {createSessionController} from './session_control_v1.mjs';
import {probeWebCompanionExtension} from './companion_external_probe.mjs';
const RUN_KEY='hermesAutopilotRunV2';
const OWNED_KEY='hermesAutopilotOwnedTabsV2';
const DRAFT_KEY='hermesAutopilotDraftFencesV1';
// Browser readiness metadata is owned by session_control_v1.mjs.
const ALARM='hermes-autopilot-reconcile-v2';
const CHAT_URL='https://chatgpt.com/';
let inflight=Promise.resolve();
const withLock=job=>{const next=inflight.catch(()=>{}).then(job);inflight=next.catch(()=>{});return next;};
const readRun=async()=>{const run=(await chrome.storage.local.get(RUN_KEY))[RUN_KEY]??null;if(run)validateRunSnapshot(run);return run;};
const saveRun=async run=>{validateRunSnapshot(run);return chrome.storage.local.set({[RUN_KEY]:run});};
const readOwned=async()=> (await chrome.storage.session.get(OWNED_KEY))[OWNED_KEY]??{};
async function addOwned(runId,workerId,tabId){const owned=await readOwned();owned[String(tabId)]={runId,workerId};await chrome.storage.session.set({[OWNED_KEY]:owned});}
async function removeOwned(tabId){const owned=await readOwned();delete owned[String(tabId)];await chrome.storage.session.set({[OWNED_KEY]:owned});}
const sessions=createSessionController({chromeAPI:chrome,readRun,readOwned,safeClose,
  probeCompanion:()=>probeWebCompanionExtension({runtime:chrome.runtime})});
const isChatURL=value=>{try{return typeof value==='string'&&value.length>0&&new URL(value).origin==='https://chatgpt.com';}catch{return false;}};
const isChatTab=tab=>{
 const active=tab?.url||'',pending=tab?.pendingUrl||'';
 // Chrome can temporarily return an empty URL or about:blank while the ChatGPT
 // navigation is pending. Never accept a known non-ChatGPT final destination.
 if(active&&active!=='about:blank')return isChatURL(active)&&(!pending||isChatURL(pending));
 return isChatURL(pending);
};
async function safeClose(runId,workerId,tabId){
 if(!Number.isInteger(tabId))return false;
 // No tab closure when a send outcome is unknown even if the task has moved.
 if(await sessions.hasUnknownSend(runId,workerId))return false;
 const owned=await readOwned();
 if(owned[String(tabId)]?.runId!==runId||owned[String(tabId)]?.workerId!==workerId)return false;
 // Bounded tab-URL settlement: do not leave an owned tab behind solely because
 // chrome.tabs.get raced a new-tab navigation. No chat messages are retried.
 for(let attempt=0;attempt<6;attempt++){
  let tab;
  try{tab=await chrome.tabs.get(tabId);}
  catch(err){
   // A transient browser API failure is NOT proof that the tab vanished.
   // Preserve its ownership lease so recovery cannot adopt or duplicate it.
   if(/No tab with id|Missing tab|tab not found/i.test(String(err?.message??err)))
    await removeOwned(tabId);
   return false;
  }
  if(isChatTab(tab)){
   await chrome.tabs.remove(tabId);await removeOwned(tabId);return true;
  }
  // If browser evidence shows navigation elsewhere, fail closed immediately.
  const active=tab?.url||'',pending=tab?.pendingUrl||'';
  if((active&&active!=='about:blank')||pending)return false;
  if(attempt<5)await new Promise(resolve=>setTimeout(resolve,150));
 }
 return false;
}
async function bindMissingSlots(input){
 let run=input;
 for(const slot of run.slots){
  if(run.slots.findIndex(s=>s.id===slot.id)>=run.maxConcurrent||slot.tabId!==null||slot.phase!=='AWAITING_TAB')continue;
  // Intent already persisted. Tab creation itself is not atomic with chrome.storage.
  const tab=await chrome.tabs.create({url:CHAT_URL,active:false});
  // Persist worker lease before tagging ownership; if interrupted here, fail closed.
  run=connectTab(run,slot.id,tab.id);await saveRun(run);
  await addOwned(run.id,slot.id,tab.id);
 }
 return run;
}
async function archiveStoppedRun(previous){
 if(!previous||previous.status!=='STOPPED')return;
 const owned=await readOwned();
 // Any still-open tab owned by a past run may contain an unsent draft
 // or unfinished conversation. It cannot be silently abandoned.
 for(const slot of previous.slots){
  if(!Number.isInteger(slot.tabId))continue;
  const ref=owned[String(slot.tabId)];
  // A lost volatile ownership record is not proof that this tab is closed.
  let live=false;
  try{const tab=await chrome.tabs.get(slot.tabId);live=!!tab;}
  catch(e){
   if(!/No tab with id|Missing tab|tab not found/i.test(String(e?.message??e)))
    throw Error('PREVIOUS_TAB_STATE_UNCERTAIN');
  }
  if(live)throw Error('PREVIOUS_RUN_TABS_STILL_OPEN_REQUIRES_REVIEW');
 }
 const key='hermesAutopilotArchivedRunV1_'+previous.id;
 const already=(await chrome.storage.local.get(key))[key];
 if(already!==undefined)return;
 const indexKey='hermesAutopilotArchiveIndexV1';
 const index=(await chrome.storage.local.get(indexKey))[indexKey]||[];
 if(!Array.isArray(index)||index.length>=10)
  throw Error('RUN_ARCHIVE_LIMIT_REQUIRES_OWNER_CLEANUP');
 const archive={schema:1,run:previous,createdAt:Date.now(),
   reason:'STOPPED_BEFORE_NEW_RUN'};
 await chrome.storage.local.set({[key]:archive});
 const saved=(await chrome.storage.local.get(key))[key];
 if(saved?.run?.id!==previous.id||saved.run.status!=='STOPPED')
  throw Error('PREVIOUS_RUN_ARCHIVE_UNCONFIRMED');
 await chrome.storage.local.set({[indexKey]:[...index,previous.id]});
}
async function start(goal,tasks,maxConcurrent=5){
 const previous=await readRun();
 if(previous&&previous.status!=='STOPPED')throw new Error('Существующий запуск ещё активен. Останови его перед новым.');
 if(previous?.status==='STOPPED')await archiveStoppedRun(previous);
 let run=createRun({id:`run_${crypto.randomUUID().replaceAll('-','')}`,goal,tasks,maxConcurrent});
 run.status='STARTING';await saveRun(run);
 try{
  run=await bindMissingSlots(run);run.status='ACTIVE';await saveRun(run);
  const out=schedule(run);await saveRun(out.run);
  // Do not automatically send any messages or focus tabs.
  const autoDraft=await autoDraftReady().catch(()=>({phase:'NOT_VERIFIED'}));
  return {summary:summarizeRun(out.run),assignments:out.assignments,
   mode:'AUTO_DRAFT_ONLY',messagingEnabled:false,autoDraft};
 }catch(err){
  const latest=await readRun();if(latest&&latest.id===run.id){latest.status='ERROR_START';await saveRun(latest);}
  throw err;
 }
}
async function retryStart(){
 let run=await readRun();if(!run||run.status!=='ERROR_START')throw new Error('Нет незавершённого запуска');
 // Do not trust a saved tab ID if browser-session ownership evidence was lost.
 const owned=await readOwned();
 for(const slot of run.slots){
  if(slot.tabId===null)continue;
  if(owned[String(slot.tabId)]?.runId!==run.id||owned[String(slot.tabId)]?.workerId!==slot.id)
   throw new Error('Вкладка есть в записи, но доказательство владения потеряно. Останови запуск, проверь вкладки вручную и создай новый запуск.');
  try{if(!isChatTab(await chrome.tabs.get(slot.tabId)))throw Error('Tab navigated away');}
  catch{throw new Error('Сохранённая вкладка недоступна. Останови запуск и создай новый.');}
 }
 try{run=await bindMissingSlots(run);run.status='ACTIVE';await saveRun(run);const out=schedule(run);await saveRun(out.run);return {summary:summarizeRun(out.run),assignments:out.assignments};}
 catch(err){const latest=await readRun();if(latest){latest.status='ERROR_START';await saveRun(latest);}throw err;}
}
async function pause(){const run=await readRun();if(!run)throw new Error('Нет запуска');const next=ownerPause(run);await saveRun(next);return summarizeRun(next);}
async function resume(){const run=await readRun();if(!run)throw new Error('Нет запуска');let next=resumeFromPause(run,{reason:'OWNER_CONFIRMED_RESUMPTION'});await saveRun(next);next=await bindMissingSlots(next);const out=schedule(next);await saveRun(out.run);return summarizeRun(out.run);}
async function stop(){
 const run=await readRun();if(!run)return {stopped:false};
 const next=ownerStop(run);await saveRun(next); // stop dispatch BEFORE external tab close
 const result=[];
 for(const slot of next.slots){
  if(slot.taskId!==null||slot.pendingSend||slot.phase==='WORKING'||
     await sessions.hasUnknownSend(next.id,slot.id)){
    result.push({workerId:slot.id,closed:false,reason:'INCOMPLETE_TASK_PRESERVED'});
    continue;
   }
   try{result.push({workerId:slot.id,closed:await safeClose(next.id,slot.id,slot.tabId)});}
  catch{result.push({workerId:slot.id,closed:false});}
 }
 return {stopped:true,closed:result.filter(r=>r.closed).length,details:result};
}
async function reconcile(){
 let run=await readRun();if(!run)return;
 if(run.status==='STARTING'){run.status='ERROR_START';await saveRun(run);return;}
 if(!['ACTIVE','PAUSED_OWNER','PAUSED_QUOTA','PAUSED_AUTH'].includes(run.status))return;
 const owned=await readOwned();
 for(const slot of run.slots){
  if(slot.tabId===null||slot.phase==='MISSING_TAB')continue;
  // Volatile browser-session proof prevents taking over a reused tab ID after restart.
  if(owned[String(slot.tabId)]?.runId!==run.id||owned[String(slot.tabId)]?.workerId!==slot.id){run=markTabMissing(run,slot.id,slot.tabId);continue;}
  try{const tab=await chrome.tabs.get(slot.tabId);if(!isChatTab(tab)){run=markTabMissing(run,slot.id,slot.tabId);}}
  catch{run=markTabMissing(run,slot.id,slot.tabId);}
 }
 await saveRun(run);
}
/** Owner-only diagnostics. Never return prompts, checkpoint text or credentials. */
async function diagnostics(){
 const run=await readRun(), warnings=[], owned=await readOwned(), tabs=[];
 if(!run)return {mode:'SAFE_DEMO',run:null,tabs:[],warnings:['NO_RUN']};
 for(const slot of run.slots){
  const meta={workerId:slot.id,phase:slot.phase,tabId:slot.tabId,taskId:slot.taskId,owned:false,reachable:false,chatOrigin:false};
  if(Number.isInteger(slot.tabId)){
   meta.owned=owned[String(slot.tabId)]?.runId===run.id&&owned[String(slot.tabId)]?.workerId===slot.id;
   try{const t=await chrome.tabs.get(slot.tabId);meta.reachable=true;meta.chatOrigin=isChatTab(t);}catch{/* report only */}
   if(!meta.owned)warnings.push(`${slot.id}:OWNERSHIP_NOT_CONFIRMED`);
   else if(!meta.reachable)warnings.push(`${slot.id}:TAB_UNAVAILABLE`);
   else if(!meta.chatOrigin)warnings.push(`${slot.id}:TAB_NAVIGATED`);
  }
  if(slot.pendingSend||await sessions.hasUnknownSend(run.id,slot.id))
   warnings.push(`${slot.id}:SEND_RECONCILIATION_REQUIRED`);
  if(slot.phase==='BLOCKED'||slot.phase==='MISSING_TAB')warnings.push(`${slot.id}:BLOCKED`);
  tabs.push(meta);
 }
 for(const slot of run.slots){if(['WARNING','CRITICAL','CLOCK_SKEW'].includes(slot.monitor?.level))warnings.push(`${slot.id}:WATCHDOG_${slot.monitor.level}`);if(slot.monitor?.trustedAlert?.severity)warnings.push(`${slot.id}:TRUSTED_${slot.monitor.trustedAlert.severity}`);}
 if(run.status==='ERROR_START')warnings.push('PARTIAL_START_REQUIRES_REVIEW');
 if(run.status==='PAUSED_QUOTA')warnings.push('ACCOUNT_QUOTA_PAUSED');
 if(run.status==='PAUSED_AUTH')warnings.push('ACCOUNT_AUTH_PAUSED');
 return {schema:4,mode:'SAFE_DEMO',run:summarizeRun(run),insights:queueInsights(run),tabs,
         tasks:run.tasks.map(t=>({id:t.id,status:t.status})),capacityAdvice:capacityAdvice(run),warnings,
         trustedTransport:{state:run.slots.some(w=>!!w.monitor?.trusted)?'SIGNED_EVENTS_OBSERVED':'NOT_CONNECTED',independentBrowserE2E:false}};
}
/** Explicit owner action: repair only genuinely missing IDLE tab, never a working chat. */
async function repairIdle(workerId){
 const run=await readRun();if(!run||run.status!=='ACTIVE')throw new Error('Восстановление доступно только для активного запуска');
 const slot=run.slots.find(w=>w.id===workerId);if(!slot)throw new Error('Неверный слот');
 if(slot.taskId!==null||!['MISSING_TAB','AWAITING_TAB'].includes(slot.phase))
  throw new Error('Нельзя автоматически восстанавливать вкладку с задачей');
 let next=run;
 if(slot.phase==='MISSING_TAB'){
  // Even without ownership proof, never open a potential duplicate while old tab exists.
  try{await chrome.tabs.get(slot.tabId);throw new Error('Старая вкладка существует. Проверь её вручную, чтобы не создавать дубль.');}
  catch(err){
   const message=String(err?.message??err);
   if(message.includes('Старая вкладка существует'))throw err;
   if(!/No tab with id|Missing tab|tab not found/i.test(message))throw new Error('Не удалось проверить старую вкладку; восстановление заблокировано');
   next=releaseIdleMissingSlot(next,workerId);await saveRun(next);
  }
 }
 // To avoid accidental five-tab relaunch, this is restricted to a single slot.
 const target=next.slots.find(w=>w.id===workerId);
 if(target.phase!=='AWAITING_TAB'||target.tabId!==null)throw new Error('Слот не готов к восстановлению');
 const tab=await chrome.tabs.create({url:CHAT_URL,active:false});
 next=connectTab(next,workerId,tab.id);await saveRun(next);await addOwned(next.id,workerId,tab.id);
 const scheduled=schedule(next);await saveRun(scheduled.run);
 return {workerId,tabId:tab.id,summary:summarizeRun(scheduled.run),assignments:scheduled.assignments,
  messagingEnabled:false};
}
/** Owner-authorized recovery after checking prior ChatGPT conversation and external effects. */
async function recoverMissing({workerId,taskId,epoch,note}){
 let run=await readRun();if(!run||run.status!=='ACTIVE')throw new Error('Запуск не активен');
 const slot=run.slots.find(s=>s.id===workerId);
 if(!slot||slot.phase!=='MISSING_TAB'||slot.taskId!==taskId||slot.epoch!==epoch)
  throw new Error('Нет соответствующей потерянной задачи');
 // A still-existing tab must never be duplicated, even if volatile ownership was lost.
 try{await chrome.tabs.get(slot.tabId);throw new Error('Старая вкладка существует: проверь её и закрой вручную перед восстановлением');}
 catch(err){
  if(String(err.message).includes('Старая вкладка существует'))throw err;
  if(!/No tab with id|Missing tab|tab not found/i.test(String(err.message)))
   throw new Error('Невозможно достоверно проверить отсутствие старой вкладки');
 }
 run=ownerRecoverMissingTask(run,{workerId,taskId,epoch,note,confirmation:'OWNER_CHECKED_HISTORY'});
 await saveRun(run); // Store released lease before non-atomic tab creation.
 await removeOwned(slot.tabId);
 const tab=await chrome.tabs.create({url:CHAT_URL,active:false});
 run=connectTab(run,workerId,tab.id);await saveRun(run);await addOwned(run.id,workerId,tab.id);
 const out=schedule(run);await saveRun(out.run);
 return {summary:summarizeRun(out.run),insights:queueInsights(out.run),assignments:out.assignments,
  recoveredTask:taskId,priorCheckpointRetained:true};
}
function previewPlan(tasks,maxConcurrent=5){
 validatePlan(tasks);return {valid:true,...analyzePlan(tasks,workerLimit(maxConcurrent)),messagingEnabled:false};
}
async function changeConcurrency(value){
 let run=await readRun();if(!run)throw new Error('Нет запуска');
 run=ownerSetConcurrency(run,value);await saveRun(run);
  if(run.status==='ACTIVE')run=await bindMissingSlots(run);
  const out=schedule(run);await saveRun(out.run);
 return {summary:summarizeRun(out.run),insights:queueInsights(out.run),assignments:out.assignments};
}
async function ownerApprove({workerId,taskId,epoch,note}){
 const run=await readRun();if(!run)throw new Error('Нет запуска');
 const slot=run.slots.find(w=>w.id===workerId);
 if(!slot||slot.taskId!==taskId||slot.epoch!==epoch)throw new Error('Назначение изменилось. Обнови интерфейс.');
 const owned=await readOwned();
 if(owned[String(slot.tabId)]?.runId!==run.id||owned[String(slot.tabId)]?.workerId!==workerId)
  throw new Error('Владение вкладкой не подтверждено');
 let tab;
 try{tab=await chrome.tabs.get(slot.tabId);}catch{throw new Error('Вкладка задачи недоступна');}
 if(!isChatTab(tab))throw new Error('Вкладка задачи больше не ChatGPT');
 // Human confirmation only: never label this as an independent VERIFIED receipt.
 const approved=ownerApproveTask(run,{workerId,taskId,epoch,note,confirmation:'OWNER_REVIEWED_OUTPUT'});
 const out=schedule(approved);await saveRun(out.run);
 return {summary:summarizeRun(out.run),insights:queueInsights(out.run),assignments:out.assignments};
}

/** Passive check only: never retries browser sends or changes task ownership. */
async function watchdogNow(){
 const run=await readRun();if(!run)return {alerts:[],watchdog:'NO_RUN'};
 const scan=watchdogScan(run);
 const trusted=trustedLivenessScan(scan.run);
 if(trusted.run.revision!==run.revision)await saveRun(trusted.run);
 return {alerts:[...scan.alerts,...trusted.alerts],watchdog:'PASSIVE',
  trustedEventsObserved:trusted.run.slots.some(w=>!!w.monitor?.trusted),
  capacityAdvice:capacityAdvice(trusted.run)};
}
/** Explicit owner action; browser reachability + lease proof checked again. */
async function acknowledgeOwnerProgress({workerId,taskId,epoch}){
 const run=await readRun();if(!run)throw Error('Нет запуска');
 const w=run.slots.find(s=>s.id===workerId);
 if(!w||w.taskId!==taskId||w.epoch!==epoch)throw Error('Назначение изменилось');
 const owned=await readOwned();
 if(owned[String(w.tabId)]?.runId!==run.id||owned[String(w.tabId)]?.workerId!==workerId)throw Error('Владение вкладкой не подтверждено');
 let tab;try{tab=await chrome.tabs.get(w.tabId);}catch{throw Error('Вкладка недоступна');}
 if(!isChatTab(tab))throw Error('Вкладка задачи больше не ChatGPT');
 const next=ownerAcknowledgeProgress(run,{workerId,taskId,epoch,confirmation:'OWNER_OBSERVED_PROGRESS'});
 await saveRun(next);return {acknowledged:true,lease:{workerId,taskId,epoch},status:'OWNER_OBSERVED_ONLY'};
}

/** Explicit owner-only fenced draft insertion; not a message send. */
async function ownerFillDraft({workerId,taskId,epoch}){
 const run=await readRun();
 if(!run||run.status!=='ACTIVE')throw Error('RUN_NOT_ACTIVE');
 const slot=run.slots.find(s=>s.id===workerId);
 const task=run.tasks.find(t=>t.id===taskId);
 if(!slot||!task||slot.phase!=='WORKING'||slot.taskId!==taskId||
    slot.epoch!==epoch||task.status!=='RUNNING'||slot.pendingSend)
   throw Error('STALE_OR_UNASSIGNED_DRAFT');
 if(!Number.isInteger(slot.tabId)||slot.tabId<1)throw Error('INVALID_OWNED_TAB');
 const owned=await readOwned();
 if(owned[String(slot.tabId)]?.runId!==run.id||owned[String(slot.tabId)]?.workerId!==workerId)
   throw Error('TAB_OWNERSHIP_UNCONFIRMED');
 let tab;
 try{tab=await chrome.tabs.get(slot.tabId);}catch{throw Error('TAB_NOT_FOUND');}
 if(!isChatTab(tab))throw Error('TAB_ORIGIN_CHANGED');
 // Wait for a fully committed page before consuming the one-shot draft fence.
 for(let tries=0;tries<24&&tab.status!=='complete';tries++){
  await new Promise(resolve=>setTimeout(resolve,250));
  tab=await chrome.tabs.get(slot.tabId);
  if(!isChatTab(tab))throw Error('TAB_ORIGIN_CHANGED_DURING_LOAD');
 }
 if(tab.status!=='complete')return {status:'TAB_STILL_LOADING',submitted:false,mayRetryAfterManualCheck:true};
 const path=new URL(tab.pendingUrl||tab.url).pathname;
 if(!(path==='/'||/^\/c\/[A-Za-z0-9_-]+\/?$/.test(path)))
   throw Error('CHAT_TAB_NOT_SUPPORTED');
 const draft=['Задача '+task.id,task.prompt,'',
   'Критерии приёмки:',...task.acceptance.map((v,i)=>String(i+1)+'. '+v)].join('\n');
 if(draft.length>16000)throw Error('DRAFT_TOO_LONG');
  const ready=await sessions.inspect(workerId,{maxAttempts:8,timeoutMs:15000});
  if(ready.state!=='READY'||ready.editorEmpty!==true||ready.hasAttachments===true||
     ready.chatModeConfirmed!==true)
   return {status:'CHAT_EDITOR_NOT_READY',diagnostic:ready.state,
     submitted:false,mayRetryAfterManualCheck:true};
 const marker=JSON.stringify([run.id,workerId,taskId,epoch,slot.tabId]);
 const rec=(await chrome.storage.local.get(DRAFT_KEY))[DRAFT_KEY]??{};
 if(!rec||Array.isArray(rec)||typeof rec!=='object')throw Error('DRAFT_RECORD_CORRUPT');
 if(Object.hasOwn(rec,marker))throw Error('DRAFT_ALREADY_ATTEMPTED_VERIFY_CHAT');
 if(Object.keys(rec).length>=500)throw Error('DRAFT_LEDGER_FULL_REQUIRES_OWNER_REVIEW');
 // Write UNKNOWN before any injected DOM effect; never retry after uncertainty.
 rec[marker]={state:'UNKNOWN',runId:run.id,workerId,taskId,epoch,
              tabId:slot.tabId,startedAt:Date.now()};
 await chrome.storage.local.set({[DRAFT_KEY]:rec});
 let proof;
 try{
  const response=await chrome.scripting.executeScript({
    target:{tabId:slot.tabId,frameIds:[0]},
    func:insertChatDraft,args:[draft]
  });
  if(response?.length!==1||response[0].frameId!==0||
      response[0].documentId!==ready.documentId)
    return {status:'DRAFT_OUTCOME_UNKNOWN',diagnostic:'DOCUMENT_CHANGED',
      submitted:false,requiresManualReview:true};
   proof=response[0].result;
 }catch{
  return {status:'DRAFT_OUTCOME_UNKNOWN',diagnostic:'INJECTION_ERROR',submitted:false,requiresManualReview:true};
 }
 const noMutation=new Set(['WRONG_ORIGIN','INVALID_DRAFT','DOCUMENT_NOT_READY',
   'MULTIPLE_EDITORS','NO_EDITOR','EDITOR_NOT_CONNECTED','EDITOR_HIDDEN',
   'EDITOR_NOT_VISIBLE','EDITOR_DISABLED','NOT_EDITABLE',
   'EXISTING_USER_TEXT','EXISTING_ATTACHMENT','TEXTAREA_SETTER_MISSING']);
 if(proof?.ok!==true){
  if(noMutation.has(proof?.reason)){
   delete rec[marker];
   await chrome.storage.local.set({[DRAFT_KEY]:rec});
   return {status:proof.reason,submitted:false,mayRetryAfterManualCheck:true};
  }
  return {status:'DRAFT_OUTCOME_UNKNOWN',diagnostic:proof?.reason??'NO_SCRIPT_RECEIPT',submitted:false,requiresManualReview:true};
 }
 const latest=await readRun();
 const current=latest?.slots.find(s=>s.id===workerId);
 let stillTab=false;
 try{stillTab=isChatTab(await chrome.tabs.get(slot.tabId));}catch{}
 if(latest?.id!==run.id||latest?.status!=='ACTIVE'||current?.taskId!==taskId||
   current?.epoch!==epoch||current?.tabId!==slot.tabId||!stillTab)
  return {status:'DRAFT_WRITTEN_LEASE_CHANGED',submitted:false,requiresManualReview:true};
 rec[marker]={...rec[marker],state:'FILLED_NOT_SENT',verifiedAt:Date.now()};
 await chrome.storage.local.set({[DRAFT_KEY]:rec});
 return {status:'DRAFT_FILLED_NOT_SENT',workerId,taskId,tabId:slot.tabId,
   submitted:false,autoSendEnabled:false};
}
async function autoDraftReady(){
 const run=await readRun();
 if(!run||run.status!=='ACTIVE')return {phase:'INACTIVE',attempted:0};
 const stored=(await chrome.storage.local.get(DRAFT_KEY))[DRAFT_KEY]??{};
 const eligible=run.slots.slice(0,run.maxConcurrent).filter(s=>{
   if(s.phase!=='WORKING'||!s.taskId||s.pendingSend)return false;
   const marker=JSON.stringify([run.id,s.id,s.taskId,s.epoch,s.tabId]);
   return !Object.hasOwn(stored,marker);
 });
 let attempted=0,filled=0;
 // Bounded per alarm: don't stall the queue with an unresponsive browser.
 for(const s of eligible.slice(0,2)){
   try{
     const probe=await sessions.inspect(s.id,{maxAttempts:1,timeoutMs:1200});
     if(probe.state!=='READY'||probe.editorEmpty!==true||probe.hasAttachments)continue;
     const result=await ownerFillDraft({workerId:s.id,taskId:s.taskId,epoch:s.epoch});
     attempted++;if(result.status==='DRAFT_FILLED_NOT_SENT')filled++;
   }catch{/* Diagnostics track readiness; never restart or send on error. */}
 }
 return {phase:'DRAFT_ONLY_NO_SEND',eligible:eligible.length,attempted,filled};
}
async function ownerFillAllDrafts(){
 const run=await readRun();
 if(!run||run.status!=='ACTIVE')throw Error('RUN_NOT_ACTIVE');
 const slots=run.slots.filter(s=>s.phase==='WORKING'&&s.taskId);
 const results=[];
 for(const slot of slots){
  try{
   let answer=await ownerFillDraft({workerId:slot.id,taskId:slot.taskId,epoch:slot.epoch});
   // Retry ONLY proven pre-write outcomes while the UI is still hydrating.
   // Never retry after execution uncertainty or a partial draft.
   const safeRetry=new Set(['NO_EDITOR','DOCUMENT_NOT_READY','EDITOR_NOT_VISIBLE','TAB_STILL_LOADING']);
   for(let n=0;n<7&&safeRetry.has(answer.status);n++){
    await new Promise(resolve=>setTimeout(resolve,450));
    answer=await ownerFillDraft({workerId:slot.id,taskId:slot.taskId,epoch:slot.epoch});
   }
   results.push({workerId:slot.id,...answer});
  }catch(error){results.push({workerId:slot.id,status:'NOT_FILLED',submitted:false,
    reason:String(error?.message??'UNKNOWN')});}
 }
 return {mode:'DRAFT_ONLY',submitted:false,
  total:results.length,filled:results.filter(x=>x.status==='DRAFT_FILLED_NOT_SENT').length,
  results};
}

async function dispatch(msg){
 switch(msg?.type){
  case 'START':return start(msg.goal,msg.tasks,msg.maxConcurrent??5);
  case 'PREVIEW_PLAN':return previewPlan(msg.tasks,msg.maxConcurrent??5);
  case 'SET_CONCURRENCY':return changeConcurrency(msg.maxConcurrent);
  case 'OWNER_APPROVE':return ownerApprove(msg);
  case 'DRAFT_TASK':return ownerFillDraft(msg);
  case 'DRAFT_ALL':return ownerFillAllDrafts();
   case 'A2A_SESSION_V1':return sessions.control(msg.request);
   case 'SESSION_READINESS_SCAN':return sessions.scan();
  case 'RECOVER_MISSING':return recoverMissing(msg);
  case 'DIAGNOSTICS':return diagnostics();
  case 'WATCHDOG_SCAN':return watchdogNow();
  case 'ACK_PROGRESS':return acknowledgeOwnerProgress(msg);
  // Break-glass read-only export, intentionally bypassing normal snapshot validation.
  // Only this trusted extension popup can request it; it never mutates or repairs state.
  case 'EXPORT_RAW':{const raw=(await chrome.storage.local.get(RUN_KEY))[RUN_KEY]??null;return {schema:1,kind:'UNMODIFIED_RECOVERY_EXPORT',run:raw,integrityUnverified:true};}
  case 'STATE_HEALTH':{const raw=(await chrome.storage.local.get(RUN_KEY))[RUN_KEY]??null;try{if(raw)validateRunSnapshot(raw);return {valid:true,present:!!raw,schema:raw?.schema??null};}catch(e){return {valid:false,present:true,error:String(e.message),rawBytes:JSON.stringify(raw).length};}}
  case 'TIMELINE':{const run=await readRun();return {mode:'SAFE_DEMO',...auditInsights(run)};}
  case 'REPAIR_IDLE':return repairIdle(msg.workerId);
  case 'COMPANION_PROBE':return probeCompanion();
   case 'RELAY_CAPACITY_PROBE':return readRelayCapacity();
   case 'HANDOFF_PREVIEW':{const run=await readRun();if(!run)throw Error('NO_ACTIVE_RUN');const slot=run.slots.find(s=>s.id===msg.workerId);return previewTurnBinding(run,{workerId:msg.workerId,taskId:msg.taskId,epoch:msg.epoch,tabId:slot?.tabId});}
   case 'STATUS':{const run=await readRun();return {run,summary:summarizeRun(run),insights:run?queueInsights(run):null,capacityAdvice:run?capacityAdvice(run):null,
    browserSessions:run&&run.status!=='STOPPED'?await sessions.control({schema:1,role:'A2',op:'STATUS',runId:run.id}):null,
    mode:'AUTO_DRAFT_ONLY',messagingEnabled:false};}
  case 'PAUSE':return pause();
  case 'RESUME':return resume();
  case 'STOP':return stop();
  case 'RETRY_START':return retryStart();
  case 'EXPORT':{const run=await readRun();if(!run)throw new Error('Нет запуска');return {schema:3,exportedAt:new Date().toISOString(),mode:'SAFE_DEMO',run};}
  // Explicitly reject privileged model events, verifier results and rotation receipts.
  // A future trusted bridge must be separate from the popup and authenticated.
  case 'EVENT':case 'VERIFY':case 'ROTATE_PREPARE':case 'ROTATE_CONFIRM':
   throw new Error('SAFE_DEMO: требуется проверенный Web Companion адаптер');
  default:throw new Error('Неизвестная команда');
 }
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id||sender.url&&!sender.url.startsWith(`chrome-extension://${chrome.runtime.id}/`)){
  respond({ok:false,error:'Ненадёжный отправитель'});return false;
 }
 withLock(()=>dispatch(message)).then(result=>respond({ok:true,result}),err=>respond({ok:false,error:String(err?.message??err)}));
 return true;
});
chrome.runtime.onInstalled.addListener(()=>{chrome.alarms.create(ALARM,{periodInMinutes:1});});
chrome.runtime.onStartup.addListener(()=>{chrome.alarms.create(ALARM,{periodInMinutes:1});withLock(async()=>{await reconcile();await sessions.scan();}).catch(()=>{});});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===ALARM)withLock(async()=>{await reconcile();await sessions.scan();await autoDraftReady();await watchdogNow();}).catch(()=>{});});
chrome.tabs.onRemoved.addListener(tabId=>{withLock(async()=>{const run=await readRun();if(!run)return;const slot=run.slots.find(s=>s.tabId===tabId);if(slot&&run.status!=='STOPPED'){await saveRun(markTabMissing(run,slot.id,tabId));}await removeOwned(tabId);}).catch(()=>{});});
