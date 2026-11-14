/**
 * Hermes AutoPilot session-control v1 for A2/A3.
 *
 * This EXTENDS the existing v0.8.5 supervisor, not a second queue/transport.
 * Browser readiness is evidence; it never equals a message send or answer.
 * The only future send path is a separately authenticated Web Companion
 * adapter. No DOM click/Enter fallback, and no retry on unknown delivery.
 */
import {ensureChatReady} from './browser_ready.mjs';
import {previewTurnBinding} from './handoff_contract.mjs';

export const SESSION_API_VERSION=1;
const RECORD_KEY='hermesAutopilotBrowserHealthV1';
const SEND_PREFIX='hermesAutopilotSendIntentV1_';
const RETRYABLE=new Set(['TAB_LOADING','COMPOSER_NOT_READY','CHAT_READY_TIMEOUT','DOM_PROBE_FAILED']);
const hex=/^[0-9a-f]{64}$/;
function fail(code){throw Error(code);}
function own(run,owned,workerId){
  const slot=run?.slots?.find(s=>s.id===workerId);
  if(!slot || !Number.isInteger(slot.tabId) || slot.tabId<=0)fail('SESSION_NOT_ALLOCATED');
  if(owned?.[String(slot.tabId)]?.runId!==run.id ||
    owned?.[String(slot.tabId)]?.workerId!==workerId)fail('SESSION_OWNERSHIP_MISSING');
  return slot;
}
function concise(value){
  return {state:value.state,attempts:value.attempts||0,
    editorEmpty:value.editorEmpty,
    hasAttachments:value.hasAttachments,
    chatModeConfirmed:value.chatModeConfirmed,
    hasModeControl:value.hasModeControl,
    documentId:value.documentId||null,
    lastObservation:value.lastObservation,
    modeSwitchRequested:value.modeSwitchRequested};
}
export function createSessionController({
  chromeAPI,readRun,readOwned,safeClose,
  relayAdapter=null,clock=()=>Date.now(),sleep
}={}){
  if(!chromeAPI?.storage?.local?.get||!chromeAPI?.storage?.local?.set ||
    !chromeAPI?.tabs?.get || typeof readRun!=='function' ||
    typeof readOwned!=='function' || typeof safeClose!=='function' ||
    typeof clock!=='function')fail('SESSION_CONTROLLER_CONFIG_INVALID');
  async function load(runId){
    const rec=(await chromeAPI.storage.local.get(RECORD_KEY))[RECORD_KEY];
    return rec?.schema===1&&rec.runId===runId&&rec.sessions&&
      !Array.isArray(rec.sessions) ? rec : {schema:1,runId,sessions:{}};
  }
  async function inspect(workerId,{maxAttempts=1,timeoutMs=1200}={}){
    const run=await readRun();if(!run||run.status==='STOPPED')fail('RUN_NOT_ACTIVE');
    const owned=await readOwned(),slot=own(run,owned,workerId);
    const rec=await load(run.id);
    const existing=rec.sessions[workerId];
    const now=clock();
    const switchCount=existing?.tabId===slot.tabId ? (existing.modeSwitches||0) : 0;
    const allowModeSwitch=switchCount<2 &&
      (!existing?.lastModeSwitchMs || now-existing.lastModeSwitchMs>=30000);
    const observation=await ensureChatReady({
      chromeAPI,tabId:slot.tabId,
      assertOwned:async()=>{
        const latest=await readRun(),proof=await readOwned();
        return latest?.id===run.id && latest.status!=='STOPPED' &&
          latest.slots?.some(s=>s.id===workerId && s.tabId===slot.tabId) &&
          proof?.[String(slot.tabId)]?.runId===run.id &&
          proof?.[String(slot.tabId)]?.workerId===workerId;
      },timeoutMs,maxAttempts,allowModeSwitch,
      ...(sleep?{sleep}:{})
    });
    const sameTab=existing?.tabId===slot.tabId;
    const state={...concise(observation),tabId:slot.tabId,
      observedAt:now,
      modeSwitches:(sameTab ? (existing.modeSwitches||0):0)+
        (observation.modeSwitchRequested===true?1:0),
      lastModeSwitchMs:observation.modeSwitchRequested===true?
        now : (sameTab?existing.lastModeSwitchMs:0),
      // Record an error streak for bounded IDLE-only reload.
      errorStreak: observation.state==='READY'?0:
        (sameTab?(existing.errorStreak||0):0)+1,
      reloads:sameTab?(existing.reloads||0):0,
      lastReloadAt:sameTab?(existing.lastReloadAt||0):0
    };
    // This ledger is metadata only; never persist composer contents.
    rec.sessions[workerId]=state;
    for(const key of Object.keys(rec.sessions))
      if(!run.slots.some(x=>x.id===key))delete rec.sessions[key];
    await chromeAPI.storage.local.set({[RECORD_KEY]:rec});
    return {schema:1,runId:run.id,workerId,phase:'READINESS_OBSERVED',
      ...concise(observation),tabId:slot.tabId};
  }
  async function scan(){
    const run=await readRun();
    if(!run||run.status!=='ACTIVE')return {schema:1,mode:'PASSIVE',items:[]};
    const proof=await readOwned(),results=[];
    for(const slot of run.slots.slice(0,run.maxConcurrent)){
      if(!Number.isInteger(slot.tabId))continue;
      if(proof?.[String(slot.tabId)]?.runId!==run.id ||
         proof?.[String(slot.tabId)]?.workerId!==slot.id){
        results.push({workerId:slot.id,state:'OWNERSHIP_LOST'});
        continue;
      }
      let health;
      try{health=await inspect(slot.id,{maxAttempts:1,timeoutMs:1200});}
      catch{results.push({workerId:slot.id,state:'READINESS_PROBE_ERROR'});continue;}
      results.push({workerId:slot.id,state:health.state});
      const rec=await load(run.id),entry=rec.sessions[slot.id];
      // An assigned task may contain an unsent draft or unknown send.
      // Never auto-reload an assigned, paused, or disconnected session.
      if(slot.taskId!==null || slot.pendingSend ||
         slot.phase!=='IDLE' || !RETRYABLE.has(health.state) ||
         entry.errorStreak<3 || entry.reloads>=2 ||
         clock()-(entry.lastReloadAt||0)<60000)continue;
      const latest=await readRun(),owned=await readOwned();
      if(latest?.id!==run.id || latest.status!=='ACTIVE')continue;
      const current=latest.slots.find(x=>x.id===slot.id);
      if(!current||current.tabId!==slot.tabId||
         current.phase!=='IDLE'||current.taskId!==null||current.pendingSend||
         owned?.[String(slot.tabId)]?.runId!==run.id)continue;
      let tab;try{tab=await chromeAPI.tabs.get(slot.tabId);}catch{continue;}
      if(new URL(tab.url||'about:blank').origin!=='https://chatgpt.com')continue;
      // Persist UNKNOWN before reload, preventing infinite retries on error.
      entry.reloads++;entry.lastReloadAt=clock();entry.state='RELOAD_UNKNOWN';
      await chromeAPI.storage.local.set({[RECORD_KEY]:rec});
      try{
        await chromeAPI.tabs.reload(slot.tabId,{bypassCache:false});
        results[results.length-1].state='IDLE_RELOAD_REQUESTED';
      }catch{results[results.length-1].state='IDLE_RELOAD_UNKNOWN';}
    }
    return {schema:1,mode:'PASSIVE',items:results};
  }
  async function control(req){
    if(!req||req.schema!==1||!['A2','A3'].includes(req.role) ||
       typeof req.op!=='string')fail('SESSION_API_REQUEST_INVALID');
    const run=await readRun();
    if(!run || run.status==='STOPPED')return {
      schema:1,state:'NO_ACTIVE_RUN',accepted:false};
    if(req.runId!==run.id)fail('RUN_ID_MISMATCH');
    if(req.op==='CAPABILITIES')return {
      schema:1,state:'AVAILABLE',accepted:true,
      operations:['CAPABILITIES','STATUS','ENSURE_READY',
        'PREVIEW_TURN','SEND_INTENT','CLOSE_IDLE'],
      sendTransportConnected:typeof relayAdapter?.submit==='function',
      source:'autopilot_v0.8.5',noDirectDomSend:true};
    if(req.op==='STATUS'){
      const rec=await load(run.id);
      return {schema:1,state:'OBSERVED',accepted:true,
        runId:run.id,runStatus:run.status,
        sessions:run.slots.map(s=>({workerId:s.id,tabId:s.tabId,
          phase:s.phase,taskId:s.taskId,
          browser:rec.sessions[s.id]?.state||'NOT_PROBED'}))};
    }
    const owned=await readOwned(),slot=own(run,owned,req.workerId);
    if(req.op==='ENSURE_READY'){
      return inspect(req.workerId,{maxAttempts:Math.min(req.maxAttempts||6,8),
        timeoutMs:Math.min(req.timeoutMs||15000,30000)});
    }
    if(req.op==='CLOSE_IDLE'){
      if(slot.taskId!==null||slot.pendingSend||slot.phase!=='IDLE')
        return {schema:1,state:'INCOMPLETE_TASK_PRESERVED',accepted:false};
      const closed=await safeClose(run.id,slot.id,slot.tabId);
      return {schema:1,state:closed?'IDLE_TAB_CLOSED':'TAB_NOT_CLOSED',
        accepted:closed,tabId:slot.tabId};
    }
    if(!['PREVIEW_TURN','SEND_INTENT'].includes(req.op))
      fail('SESSION_API_UNKNOWN_OPERATION');
    if(slot.phase!=='WORKING'||slot.taskId!==req.taskId ||
       slot.epoch!==req.epoch || slot.pendingSend)
      fail('STALE_OR_UNCONFIRMED_LEASE');
    const preview=await previewTurnBinding(run,{
      workerId:slot.id,taskId:req.taskId,epoch:req.epoch,tabId:slot.tabId
    });
    if(req.op==='PREVIEW_TURN')return {
      schema:1,state:'PREVIEW_ONLY',accepted:true,turnId:preview.turnId,
      relayLeaseConfirmed:false};
    // Only Web Companion's authenticated background can own this send. A
    // caller's role string, preview turnId, or tab URL is not authorization.
    if(typeof relayAdapter?.submit!=='function')return {
      schema:1,state:'TRUSTED_WEB_COMPANION_NOT_CONNECTED',
      accepted:false,sendPerformed:false,turnId:preview.turnId};
    if(typeof req.requestId!=='string'||
       !/^[A-Za-z0-9_-]{12,128}$/.test(req.requestId))fail('REQUEST_ID_INVALID');
    const ready=await inspect(slot.id,{maxAttempts:6,timeoutMs:15000});
    if(ready.state!=='READY'||ready.editorEmpty!==true||
       ready.hasAttachments===true||
       (ready.hasModeControl===true && ready.chatModeConfirmed!==true))return {
      schema:1,state:'CHAT_NOT_READY_FOR_SEND',accepted:false,
      sendPerformed:false,lastObservation:ready.state};
    const key=SEND_PREFIX+preview.turnId;
    const original=(await chromeAPI.storage.local.get(key))[key];
    if(original!==undefined)return {schema:1,state:'UNKNOWN_SEND_RECONCILIATION_REQUIRED',
      accepted:false,sendPerformed:false};
    const nonce=crypto.randomUUID();
    const record={schema:1,state:'UNKNOWN_SEND',runId:run.id,
      workerId:slot.id,taskId:req.taskId,epoch:req.epoch,
      turnId:preview.turnId,attemptId:nonce,createdAt:clock()};
    await chromeAPI.storage.local.set({[key]:record});
    const persisted=(await chromeAPI.storage.local.get(key))[key];
    if(persisted?.attemptId!==nonce)return {schema:1,
      state:'UNKNOWN_SEND_RECONCILIATION_REQUIRED',accepted:false};
    try{
      const receipt=await relayAdapter.submit({preview,
        documentId:ready.documentId,requestId:req.requestId});
      if(receipt?.schema!==1||receipt.turnId!==preview.turnId ||
         receipt.trustedRelayLease!==true)return {
           schema:1,state:'UNKNOWN_SEND_RECONCILIATION_REQUIRED',
           accepted:false};
      return {schema:1,state:'RELAY_HANDOFF_ACK_ONLY',accepted:true,
        turnId:preview.turnId,sendPerformed:'UNVERIFIED',
        responseVerified:false,receiptId:receipt.receiptId||null};
    }catch{
      return {schema:1,state:'UNKNOWN_SEND_RECONCILIATION_REQUIRED',
        accepted:false,sendPerformed:'UNKNOWN'};
    }
  }
  return Object.freeze({inspect,scan,control});
}
