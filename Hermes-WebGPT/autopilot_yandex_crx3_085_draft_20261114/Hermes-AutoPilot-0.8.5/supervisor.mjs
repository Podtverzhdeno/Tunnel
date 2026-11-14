import {taskRank,workerLimit} from './planning.mjs';
/** Hermes AutoPilot v0.6.0 deterministic state transitions.
 * Transport, verifier and authorization are NOT implemented here.
 * Caller must authenticate EVERY worker event before invoking applyWorkerEvent.
 */
export const MAX_WORKERS=5;
export const MAX_TRANSIENT_RELOADS=2;
export const MAX_STAGNANT_TURNS=3;
export const VERSION=3;
const MAX_EVENTS=500;
const MAX_TEXT=16000;
const MAX_CLOCK_MS=8640000000000000;
function validClock(ms){return Number.isSafeInteger(ms)&&ms>=0&&ms<=MAX_CLOCK_MS;}
function monitorBase(nowMs){return {assignedAtMs:nowMs,lastCheckedAtMs:nowMs,lastOwnerAckAtMs:null,level:'OK',lastAlertAtMs:null};}
function assert(condition,message){if(!condition)throw new Error(message);}
function textOk(v,max=MAX_TEXT){return typeof v==='string'&&v.trim().length>0&&v.length<=max;}
function idOk(v){return typeof v==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(v);}
const copy=v=>structuredClone(v);
const knownStatus=new Set(['STARTING','ACTIVE','PAUSED_OWNER','PAUSED_QUOTA','PAUSED_AUTH','STOPPED','VERIFIED_DONE','OWNER_REVIEWED_DONE','ERROR_START']);
function taskById(run,id){const t=run.tasks.find(t=>t.id===id);assert(t,`Unknown task ${id}`);return t;}
function slotById(run,id){const w=run.slots.find(w=>w.id===id);assert(w,`Unknown slot ${id}`);return w;}
function record(run,type,meta={}){
 run.revision++;
 run.audit.push({revision:run.revision,type,...meta});
 if(run.audit.length>MAX_EVENTS)run.audit.splice(0,run.audit.length-MAX_EVENTS);
}
function current(run,event){
 const w=slotById(run,event.workerId);
 return w.taskId && w.epoch===event.epoch && w.taskId===event.taskId && w.tabId===event.tabId;
}
export function validatePlan(items){
 assert(Array.isArray(items)&&items.length>=1&&items.length<=100,'Plan requires 1..100 tasks');
 const ids=new Set();
 for(const task of items){
  assert(task&&typeof task==='object'&&!Array.isArray(task),'Invalid task');
  assert(idOk(task.id)&&!ids.has(task.id),'Task id must be safe and unique'); ids.add(task.id);
  assert(textOk(task.prompt),'Task prompt is missing or too long');
  assert(Array.isArray(task.acceptance)&&task.acceptance.length>0&&task.acceptance.length<=30&&task.acceptance.every(x=>textOk(x,1000)),'Invalid acceptance criteria');
  assert(Array.isArray(task.depends_on??[])&&(task.depends_on??[]).length<=100&&(task.depends_on??[]).every(idOk),'Invalid dependencies');
  assert(new Set(task.depends_on??[]).size===(task.depends_on??[]).length,'Duplicate dependency');
  assert(Number.isInteger(task.priority??3)&&(task.priority??3)>=1&&(task.priority??3)<=5,'Invalid task priority');
  assert(Number.isInteger(task.estimatedEffort??1)&&(task.estimatedEffort??1)>=1&&(task.estimatedEffort??1)<=100,'Invalid estimated effort');
  assert(Array.isArray(task.resources??[])&&(task.resources??[]).length<=20&&(task.resources??[]).every(idOk),'Invalid resources');
  assert(new Set(task.resources??[]).size===(task.resources??[]).length,'Duplicate resource');
 }
 const byId=new Map(items.map(t=>[t.id,t]));
 for(const t of items) for(const dep of t.depends_on??[]) assert(byId.has(dep)&&dep!==t.id,`Invalid dependency: ${t.id} -> ${dep}`);
 const seen=new Set(), visiting=new Set();
 const dfs=id=>{if(visiting.has(id))throw new Error(`Dependency cycle involving: ${id}`);if(seen.has(id))return;visiting.add(id);for(const dep of byId.get(id).depends_on??[])dfs(dep);visiting.delete(id);seen.add(id);};
 for(const id of ids)dfs(id);
 return true;
}
export function createRun({id,goal,tasks,maxConcurrent=MAX_WORKERS}){
 assert(idOk(id),'Invalid run id'); assert(textOk(goal,5000),'Missing or oversized goal'); validatePlan(tasks); workerLimit(maxConcurrent);
 return {schema:VERSION,id,goal,status:'ACTIVE',revision:0,maxConcurrent,
  slots:Array.from({length:MAX_WORKERS},(_,i)=>({id:`worker-${i+1}`,tabId:null,taskId:null,epoch:0,phase:'AWAITING_TAB',checkpoint:null,checkpointVersion:0,retries:0,stalled:0,lastProgressFingerprint:null,pendingSend:null,rotation:null,sessionId:null})),
  tasks:tasks.map(t=>({id:t.id,prompt:t.prompt,depends_on:[...(t.depends_on??[])],resources:[...(t.resources??[])],priority:t.priority??3,estimatedEffort:t.estimatedEffort??1,acceptance:[...t.acceptance],status:'PENDING',evidence:[]})),
  audit:[],seenEventIds:[]};
}
export function connectTab(input,workerId,tabId){
 const run=copy(input),w=slotById(run,workerId);
 assert(Number.isInteger(tabId)&&tabId>0,'Invalid tab id');
 assert(!run.slots.some(s=>s!==w&&s.tabId===tabId),'Tab already belongs to another worker');
 assert(w.phase==='AWAITING_TAB'&&w.tabId===null,'Slot is already bound');
 assert(['ACTIVE','STARTING','ERROR_START'].includes(run.status),'Cannot connect tab in this status');
 w.tabId=tabId;w.phase='IDLE';record(run,'TAB_BOUND',{workerId,tabId});return run;
}
export function schedule(input,{nowMs=Date.now()}={}){
 assert(validClock(nowMs),'Invalid scheduling clock');
 const run=copy(input),assignments=[];
 if(run.status!=='ACTIVE')return {run,assignments};
 // Any unfinished work (including BLOCKED after lost tab or verifier failure) retains exclusive resources.
 const busy=new Set(run.tasks.filter(t=>['RUNNING','VERIFYING','BLOCKED'].includes(t.status)).flatMap(t=>t.resources));
 const ordered=taskRank(run.tasks).ordered;
 let occupied=run.slots.filter(s=>s.taskId!==null).length;
 const cap=workerLimit(run.maxConcurrent??MAX_WORKERS);
 for(const w of run.slots){
  if(occupied>=cap)break;
  if(w.phase!=='IDLE'||w.tabId===null)continue;
  const t=ordered.find(t=>t.status==='PENDING'&&t.depends_on.every(id=>['VERIFIED','OWNER_APPROVED'].includes(taskById(run,id).status))&&t.resources.every(r=>!busy.has(r)));
  if(!t)break;
  t.status='RUNNING'; occupied++; for(const r of t.resources)busy.add(r);
  w.taskId=t.id;w.epoch++;w.phase='WORKING';w.retries=0;w.stalled=0;w.lastProgressFingerprint=null;
  w.checkpoint=null;w.checkpointVersion=0;w.pendingSend=null;w.monitor=monitorBase(nowMs);
  assignments.push({runId:run.id,workerId:w.id,taskId:t.id,epoch:w.epoch,tabId:w.tabId,prompt:t.prompt,goal:run.goal,acceptance:[...t.acceptance],resources:[...t.resources]});
  record(run,'TASK_ASSIGNED',{workerId:w.id,taskId:t.id,epoch:w.epoch});
 }
 completeIfDone(run);
 return {run,assignments};
}
export function applyWorkerEvent(input,event){
 const run=copy(input);
 assert(event&&idOk(event.workerId)&&textOk(event.type,80),'Missing worker identity/event type');
 const w=slotById(run,event.workerId);
 if(event.runId && event.runId!==run.id)return {run,action:'IGNORED_STALE'};
 if(event.eventId!==undefined){
  assert(idOk(event.eventId),'Invalid event id');
  if(run.seenEventIds.includes(event.eventId))return {run,action:'IGNORED_DUPLICATE'};
 }
 if(!current(run,event)){
  record(run,'STALE_EVENT',{workerId:w.id,type:event.type});
  return {run,action:'IGNORED_STALE'};
 }
 // Global outage events still require a current, authenticated lease.
 if(['ACCOUNT_LIMIT','AUTH_REQUIRED'].includes(event.type)){
  if(run.status!=='ACTIVE')return {run,action:'IGNORED_PAUSED'};
  run.status=event.type==='ACCOUNT_LIMIT'?'PAUSED_QUOTA':'PAUSED_AUTH';
  record(run,event.type,{workerId:w.id});
  if(event.eventId){run.seenEventIds.push(event.eventId);if(run.seenEventIds.length>MAX_EVENTS)run.seenEventIds.shift();}
  return {run,action:'PAUSE_ALL'};
 }
 if(run.status!=='ACTIVE')return {run,action:'IGNORED_PAUSED'};
 const t=taskById(run,w.taskId);
 let action,extra={};
 switch(event.type){
 case 'CHECKPOINT':{
  assert(w.phase==='WORKING'||w.phase==='RECONCILE_REQUIRED','Checkpoint not allowed in this phase');
  const cp=event.checkpoint;
  assert(cp&&textOk(cp.summary,8000)&&textOk(cp.next_action,4000)&&Array.isArray(cp.evidence)&&cp.evidence.length<=100,'Invalid checkpoint');
  assert(Number.isInteger(cp.version)&&cp.version>w.checkpointVersion,'Checkpoint version must increase');
  assert(Array.isArray(cp.artifact_refs??[])&&Array.isArray(cp.pending_actions??[])&&(cp.artifact_refs??[]).length<=100&&(cp.pending_actions??[]).length<=100,'Invalid checkpoint arrays');
  let serialized;try{serialized=JSON.stringify(cp);}catch{throw new Error('Checkpoint is not serializable');}
  assert(typeof serialized==='string'&&serialized.length<=64000,'Checkpoint exceeds 64KB size cap');
  w.checkpoint={version:cp.version,summary:cp.summary,next_action:cp.next_action,evidence:copy(cp.evidence),artifact_refs:copy(cp.artifact_refs??[]),pending_actions:copy(cp.pending_actions??[])};
  w.checkpointVersion=cp.version;record(run,'CHECKPOINT',{workerId:w.id,taskId:t.id,version:cp.version});action='ACK_CHECKPOINT';break;
 }
 case 'SEND_STARTED':{
  assert(w.phase==='WORKING','Not accepting sends in this phase');assert(idOk(event.messageId),'Missing message id');assert(!w.pendingSend,'Unreconciled send already exists');
  w.pendingSend={messageId:event.messageId,status:'UNKNOWN'};record(run,'SEND_STARTED',{workerId:w.id,messageId:event.messageId});action='TRACK_DELIVERY';break;
 }
 case 'SEND_RECONCILED':{
  assert(w.pendingSend&&w.pendingSend.messageId===event.messageId,'No matching send');
  assert(w.phase==='WORKING'||w.phase==='RECONCILE_REQUIRED','Reconciliation not allowed in this phase');
  assert(['DELIVERED','NOT_DELIVERED'].includes(event.outcome),'Invalid send outcome');
  w.pendingSend=null;if(w.phase==='RECONCILE_REQUIRED')w.phase='WORKING';
  record(run,'SEND_RECONCILED',{workerId:w.id,messageId:event.messageId,outcome:event.outcome});
  action=event.outcome==='DELIVERED'?'WAIT_RESPONSE':'SAFE_TO_SEND_ONCE';break;
 }
 case 'HEARTBEAT':{
  // The caller must be the trusted ingress; receivedAtMs is created by the host,
  // never taken from model text. A heartbeat proves connectivity, NOT useful work.
  assert(['WORKING','VERIFYING','RECONCILE_REQUIRED'].includes(w.phase),'Heartbeat not allowed in this phase');
  assert(Number.isSafeInteger(event.sequence)&&event.sequence>0,'Heartbeat sequence must be positive');
  assert(validClock(event.receivedAtMs),'Untrusted heartbeat clock');
  if(event.fingerprint!==undefined)assert(textOk(event.fingerprint,200),'Invalid heartbeat fingerprint');
  const prior=w.monitor?.trusted??null;
  if(prior&&event.sequence<=prior.sequence)return {run,action:'IGNORED_OLD_HEARTBEAT'};
  // A trusted event cannot turn the worker clock backward, even if a local wall clock moves.
  if(prior&&event.receivedAtMs<prior.lastSeenAtMs)return {run,action:'IGNORED_BACKDATED_HEARTBEAT'};
  if(!w.monitor)w.monitor=monitorBase(event.receivedAtMs);
  const hasProgress=event.fingerprint!==undefined;
  const changed=hasProgress&&(!prior||event.fingerprint!==prior.fingerprint);
  w.monitor.trusted={sequence:event.sequence,lastSeenAtMs:event.receivedAtMs,
   lastProgressAtMs:changed||!prior?event.receivedAtMs:prior.lastProgressAtMs,
   fingerprint:hasProgress?event.fingerprint:prior?.fingerprint??null};
  record(run,'TRUSTED_HEARTBEAT',{workerId:w.id,taskId:t.id,sequence:event.sequence});
  action='ACK_HEARTBEAT';break;
 }
 case 'PROGRESS':{
  assert(w.phase==='WORKING','Progress not allowed in this phase');assert(textOk(event.fingerprint,200),'Missing/oversized progress fingerprint');
  w.stalled=event.fingerprint===w.lastProgressFingerprint?w.stalled+1:0;
  if(event.fingerprint!==w.lastProgressFingerprint)w.retries=0;
  w.lastProgressFingerprint=event.fingerprint;
  if(w.stalled>=MAX_STAGNANT_TURNS){w.phase='BLOCKED';t.status='BLOCKED';record(run,'STAGNANT_BLOCKED',{workerId:w.id,taskId:t.id});action='ESCALATE_STAGNATION';}
  else {record(run,'PROGRESS',{workerId:w.id,taskId:t.id});action='CONTINUE';} break;
 }
 case 'TRANSIENT_ERROR':{
  assert(['WORKING','RECONCILE_REQUIRED'].includes(w.phase),'Transient error not allowed in this phase');
  if(w.pendingSend){w.phase='RECONCILE_REQUIRED';record(run,'UNCERTAIN_SEND',{workerId:w.id,taskId:t.id});action='RECONCILE_BEFORE_RELOAD';break;}
  w.retries++;
  if(w.retries>MAX_TRANSIENT_RELOADS){w.phase='BLOCKED';t.status='BLOCKED';record(run,'RETRY_EXHAUSTED',{workerId:w.id});action='ESCALATE_ERROR';}
  else{record(run,'TRANSIENT_ERROR',{workerId:w.id,retry:w.retries});action='RELOAD_TAB_WITH_BACKOFF';extra={attempt:w.retries,delayMs:w.retries===1?1500:6000};}break;
 }
 case 'CONTEXT_EXHAUSTED':{
  assert(w.phase==='WORKING','Cannot rotate in this phase');
  if(!w.checkpoint||w.pendingSend){w.phase='RECONCILE_REQUIRED';record(run,'ROTATION_BLOCKED',{workerId:w.id});action='CHECKPOINT_OR_RECONCILE_FIRST';break;}
  w.phase='ROTATING';w.rotation={oldTabId:w.tabId,checkpointVersion:w.checkpointVersion};record(run,'ROTATION_STARTED',{workerId:w.id,oldTabId:w.tabId});action='OPEN_REPLACEMENT_TAB';break;
 }
 case 'COMPLETE_CANDIDATE':{
  assert(w.phase==='WORKING','Cannot complete blocked or rotating work');
  assert(Array.isArray(event.evidence)&&event.evidence.length>0&&event.evidence.length<=100,'No or invalid evidence');
  if(w.pendingSend){action='RECONCILE_BEFORE_VERIFY';break;}
  w.phase='VERIFYING';t.status='VERIFYING';t.evidence=copy(event.evidence);record(run,'COMPLETE_CANDIDATE',{workerId:w.id,taskId:t.id});action='INDEPENDENT_VERIFY';break;
 }
 default:throw new Error(`Unknown event type: ${event.type}`);
 }
 if(event.eventId){run.seenEventIds.push(event.eventId);if(run.seenEventIds.length>MAX_EVENTS)run.seenEventIds.shift();}
 return {run,action,...extra};
}
export function confirmRotation(input,{workerId,newTabId,expectedOldTabId,checkpointVersion}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Cannot rotate inactive run');assert(w.phase==='ROTATING'&&w.rotation,'No rotation pending');
 assert(w.rotation.oldTabId===expectedOldTabId&&w.rotation.checkpointVersion===checkpointVersion,'Rotation CAS mismatch');
 assert(Number.isInteger(newTabId)&&newTabId>0&&!run.slots.some(s=>s.tabId===newTabId),'Invalid replacement tab');
 const oldTabId=w.tabId;w.tabId=newTabId;w.phase='WORKING';w.rotation=null;w.epoch++;w.retries=0;w.sessionId=null;w.monitor=null;
 record(run,'ROTATION_CONFIRMED',{workerId,oldTabId,newTabId,epoch:w.epoch});
 return {run,oldTabId,lease:{workerId:w.id,taskId:w.taskId,epoch:w.epoch,tabId:w.tabId}};
}
/** Only a trusted independent verifier can call this, NEVER model text or DOM. */
export function verifiedResult(input,{workerId,taskId,epoch,passed,evidence}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Run is paused or stopped');
 assert(w.taskId===taskId&&w.epoch===epoch&&w.phase==='VERIFYING','Invalid verification lease');
 assert(typeof passed==='boolean','Verification outcome must be boolean');
 assert(Array.isArray(evidence)&&evidence.length>0,'Verification receipts required');
 const t=taskById(run,taskId);
 if(!passed){t.status='BLOCKED';w.phase='BLOCKED';record(run,'VERIFICATION_FAILED',{workerId,taskId});return run;}
 t.status='VERIFIED';t.evidence=copy(evidence);w.taskId=null;w.phase='IDLE';w.pendingSend=null;w.checkpoint=null;w.checkpointVersion=0;w.monitor=null;
 record(run,'VERIFIED',{workerId,taskId});completeIfDone(run);return run;
}
function completeIfDone(run){
 if(run.tasks.every(t=>['VERIFIED','OWNER_APPROVED'].includes(t.status))){
  run.status=run.tasks.every(t=>t.status==='VERIFIED')?'VERIFIED_DONE':'OWNER_REVIEWED_DONE';
 }
}
/** Owner-only offline workflow: explicitly confirms their own review of acceptance criteria.
 * This is intentionally different from VERIFIED (independent verifier). */
export function ownerApproveTask(input,{workerId,taskId,epoch,note,confirmation}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Run is not active');
 assert(confirmation==='OWNER_REVIEWED_OUTPUT','Explicit owner review required');
 assert(textOk(note,1200),'Owner evidence note required (max 1200 chars)');
 assert(w.taskId===taskId&&w.epoch===epoch&&w.phase==='WORKING','Current working lease required');
 assert(!w.pendingSend,'Unreconciled send blocks approval');
 const task=taskById(run,taskId);
 assert(task.status==='RUNNING','Task is not running');
 task.status='OWNER_APPROVED';task.evidence=[{type:'OWNER_REVIEW',note}];
 w.taskId=null;w.phase='IDLE';w.pendingSend=null;w.checkpoint=null;w.checkpointVersion=0;w.monitor=null;
 record(run,'OWNER_APPROVED',{workerId,taskId,epoch});completeIfDone(run);return run;
}
/** Runtime controller adjusts future dispatch; does not interrupt active work. */
export function ownerSetConcurrency(input,maxConcurrent){
 const run=copy(input);workerLimit(maxConcurrent);
 assert(['ACTIVE','PAUSED_OWNER','PAUSED_QUOTA','PAUSED_AUTH'].includes(run.status),'Run cannot be tuned in this state');
 if((run.maxConcurrent??MAX_WORKERS)!==maxConcurrent){run.maxConcurrent=maxConcurrent;record(run,'CONCURRENCY_SET',{maxConcurrent});}
 return run;
}
export function ownerPause(input){const run=copy(input);assert(run.status==='ACTIVE'||run.status==='STARTING','Cannot pause run');run.status='PAUSED_OWNER';record(run,'OWNER_PAUSED');return run;}
export function resumeFromPause(input,{reason}){
 const run=copy(input);assert(['PAUSED_QUOTA','PAUSED_AUTH','PAUSED_OWNER'].includes(run.status),'Not paused');
 assert(reason==='OWNER_CONFIRMED_RESUMPTION','Resume requires trusted confirmation');
 run.status='ACTIVE';record(run,'RESUMED');return run;
}
export function ownerStop(input){const run=copy(input);assert(knownStatus.has(run.status),'Invalid run status');if(run.status!=='STOPPED'){run.status='STOPPED';record(run,'OWNER_STOPPED');}return run;}
/** After tab disappearance, fail-closed. A worker with no task may be manually rebound. */
export function markTabMissing(input,workerId,tabId){
 const run=copy(input),w=slotById(run,workerId);if(w.tabId!==tabId)return run;
 if(w.phase==='MISSING_TAB')return run;
 w.phase='MISSING_TAB';if(w.taskId){const t=taskById(run,w.taskId);t.status='BLOCKED';}
 record(run,'TAB_MISSING',{workerId,tabId});return run;
}
/** Owner reconciled externally visible effects and confirmed that a lost-tab task may be retried.
 * This function cannot itself establish that no browser/chat send occurred. */
export function ownerRecoverMissingTask(input,{workerId,taskId,epoch,note,confirmation}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Run is not active');
 assert(confirmation==='OWNER_CHECKED_HISTORY','Manual history reconciliation confirmation required');
 assert(textOk(note,1200),'Recovery review note required');
 assert(w.phase==='MISSING_TAB'&&w.taskId===taskId&&w.epoch===epoch,'Lost-tab lease mismatch');
 assert(!w.pendingSend,'Unknown send must be externally reconciled first');
 const t=taskById(run,taskId);assert(t.status==='BLOCKED','Only blocked task can be recovered');
 t.recoveryCheckpoint=w.checkpoint?copy(w.checkpoint):null;
 t.recoveryNote=note;t.status='PENDING';t.evidence=[];
 w.taskId=null;w.tabId=null;w.epoch++;w.phase='AWAITING_TAB';w.pendingSend=null;w.monitor=null;
 w.rotation=null;w.checkpoint=null;w.checkpointVersion=0;w.retries=0;w.stalled=0;w.sessionId=null;
 record(run,'OWNER_RECOVERED_MISSING_TASK',{workerId,taskId,epoch:w.epoch});return run;
}
export function releaseIdleMissingSlot(input,workerId){
 const run=copy(input),w=slotById(run,workerId);
 assert(w.phase==='MISSING_TAB'&&!w.taskId,'Cannot release a missing tab with unfinished work');
 w.tabId=null;w.phase='AWAITING_TAB';w.sessionId=null;record(run,'MISSING_SLOT_RELEASED',{workerId});return run;
}
/** Session binding requires attestation by a separate trusted adapter. The string
 * confirmation is a sequencing invariant, not an authentication mechanism.
 * This function must NEVER be directly exposed to popup/page/model messages.
 */
export function bindTrustedSession(input,{workerId,taskId,epoch,tabId,sessionId,confirmedByAdapter}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Cannot bind session to inactive run');
 assert(confirmedByAdapter===true,'Trusted adapter attestation required');
 assert(idOk(sessionId),'Invalid session ID');
 assert(w.taskId===taskId&&w.epoch===epoch&&w.tabId===tabId,'Session lease mismatch');
 assert(['WORKING','VERIFYING','RECONCILE_REQUIRED'].includes(w.phase),'Session not working');
 assert(!w.sessionId||w.sessionId===sessionId,'Session already bound to a different conversation');
 assert(!run.slots.some(s=>s!==w&&s.sessionId===sessionId),'Conversation is already bound to another worker');
 if(w.sessionId===sessionId)return run;
 w.sessionId=sessionId;record(run,'SESSION_BOUND',{workerId,taskId,epoch});return run;
}
/** Only trusted adapter heartbeats can support this liveness classification.
 * A user's acknowledgement never masks missing adapter heartbeats.
 * Scan is passive, and cannot retry messages, reassign tasks or close tabs.
 */
export function trustedLivenessScan(input,nowMs=Date.now(),config={}){
 assert(validClock(nowMs),'Invalid liveness clock');
 const silentMs=config.silentMs??2*60*1000;
 const noProgressMs=config.noProgressMs??12*60*1000;
 const cooldownMs=config.cooldownMs??5*60*1000;
 assert(Number.isSafeInteger(silentMs)&&silentMs>=1000,'Invalid silent threshold');
 assert(Number.isSafeInteger(noProgressMs)&&noProgressMs>silentMs,'Invalid progress threshold');
 assert(Number.isSafeInteger(cooldownMs)&&cooldownMs>=1000,'Invalid cooldown');
 const run=copy(input),alerts=[];
 if(run.status!=='ACTIVE')return {run,alerts};
 for(const w of run.slots){
  if(!w.taskId||!['WORKING','VERIFYING','RECONCILE_REQUIRED'].includes(w.phase))continue;
  const h=w.monitor?.trusted;
  if(!h)continue; // no authenticated bridge observed; never infer agent liveness
  const silent=nowMs-h.lastSeenAtMs,stalled=nowMs-h.lastProgressAtMs;
  const severity=silent<0||stalled<0?'CLOCK_SKEW':silent>=silentMs?'TRUSTED_HEARTBEAT_SILENT':stalled>=noProgressMs?'TRUSTED_PROGRESS_STALLED':null;
  if(!severity){if(w.monitor.trustedAlert){w.monitor.trustedAlert=null;record(run,'TRUSTED_ACTIVITY_RECOVERED',{workerId:w.id,taskId:w.taskId});}continue;}
  const prev=w.monitor.trustedAlert;
  if(prev?.severity===severity&&nowMs>=prev.atMs&&nowMs-prev.atMs<cooldownMs)continue;
  w.monitor.trustedAlert={severity,atMs:nowMs};
  record(run,'TRUSTED_LIVENESS_ALERT',{workerId:w.id,taskId:w.taskId,severity});
  alerts.push({workerId:w.id,taskId:w.taskId,severity,silentMs:Math.max(0,silent),noProgressMs:Math.max(0,stalled)});
 }
 return {run,alerts};
}
export function summarizeRun(run){if(!run)return null;return {id:run.id,status:run.status,revision:run.revision,verified:run.tasks.filter(t=>t.status==='VERIFIED').length,ownerApproved:run.tasks.filter(t=>t.status==='OWNER_APPROVED').length,total:run.tasks.length,maxConcurrent:run.maxConcurrent??MAX_WORKERS,slots:run.slots.map(w=>({id:w.id,phase:w.phase,tabId:w.tabId,taskId:w.taskId,epoch:w.epoch,pendingSend:!!w.pendingSend,checkpointVersion:w.checkpointVersion,sessionBound:!!w.sessionId,trustedHeartbeatAtMs:w.monitor?.trusted?.lastSeenAtMs??null}))};}

/** Passive watchdog: wall-clock based owner-review reminders, NEVER evidence of live agent liveness.
 * Alerts cannot free leases, retry sends, rotate chats, or mark a task completed.
 * Old schemas without monitor start monitoring only when explicitly owner-acknowledged or newly assigned.
 */
export function watchdogScan(input,nowMs=Date.now(),config={}){
 assert(validClock(nowMs),'Invalid watchdog clock');
 const warningMs=config.warningMs??15*60*1000,criticalMs=config.criticalMs??45*60*1000;
 const cooldownMs=config.cooldownMs??15*60*1000;
 assert(Number.isSafeInteger(warningMs)&&warningMs>=60000,'Invalid warning threshold');
 assert(Number.isSafeInteger(criticalMs)&&criticalMs>warningMs,'Invalid critical threshold');
 assert(Number.isSafeInteger(cooldownMs)&&cooldownMs>=60000,'Invalid alert cooldown');
 const run=copy(input),alerts=[];
 if(run.status!=='ACTIVE')return {run,alerts};
 for(const w of run.slots){
  if(!w.taskId||!['WORKING','VERIFYING','RECONCILE_REQUIRED'].includes(w.phase))continue;
  if(!w.monitor||!validClock(w.monitor.lastCheckedAtMs))continue; // v0.4 state is not clock-trusted
  const elapsed=nowMs-w.monitor.lastCheckedAtMs;
  if(elapsed<0){if(w.monitor.level!=='CLOCK_SKEW'){
   w.monitor.level='CLOCK_SKEW';record(run,'WATCHDOG_CLOCK_SKEW',{workerId:w.id,taskId:w.taskId});
   alerts.push({workerId:w.id,taskId:w.taskId,level:'CLOCK_SKEW',reason:'WALL_CLOCK_MOVED_BACKWARDS'});
  }continue;}
  const level=elapsed>=criticalMs?'CRITICAL':elapsed>=warningMs?'WARNING':'OK';
  if(level==='OK')continue;
  const firstOrRaised=!['WARNING','CRITICAL'].includes(w.monitor.level)||level==='CRITICAL'&&w.monitor.level!=='CRITICAL';
  const cooling=validClock(w.monitor.lastAlertAtMs)&&nowMs-w.monitor.lastAlertAtMs<cooldownMs;
  if(firstOrRaised||!cooling){
   w.monitor.level=level;w.monitor.lastAlertAtMs=nowMs;
   record(run,`WATCHDOG_${level}`,{workerId:w.id,taskId:w.taskId});
   alerts.push({workerId:w.id,taskId:w.taskId,level,elapsedMs:elapsed,reason:'NO_OWNER_CONFIRMED_PROGRESS'});
  }
 }
 return {run,alerts};
}
/** Owner has EXTERNALLY checked the chat and states the lease is progressing. Not an agent heartbeat. */
export function ownerAcknowledgeProgress(input,{workerId,taskId,epoch,atMs=Date.now(),confirmation}){
 const run=copy(input),w=slotById(run,workerId);
 assert(run.status==='ACTIVE','Run is not active');
 assert(confirmation==='OWNER_OBSERVED_PROGRESS','Explicit owner observation required');
 assert(validClock(atMs),'Invalid observation clock');
 assert(w.taskId===taskId&&w.epoch===epoch&&['WORKING','VERIFYING','RECONCILE_REQUIRED'].includes(w.phase),'Lease mismatch');
 assert(!w.pendingSend,'Uncertain send must be reconciled before progress acknowledgement');
 if(w.monitor&&atMs<w.monitor.lastCheckedAtMs&&w.monitor.level!=='CLOCK_SKEW')throw Error('Observation clock moved backwards');
 // Once skew is identified, an explicit owner observation rebases the wall-clock monitor.
 // Epoch and lease fencing still apply; no task or external side effect is replayed.
 if(!w.monitor)w.monitor=monitorBase(atMs);
 w.monitor.lastCheckedAtMs=atMs;w.monitor.lastOwnerAckAtMs=atMs;
 w.monitor.lastAlertAtMs=null;w.monitor.level='OK';
 record(run,'OWNER_PROGRESS_OBSERVED',{workerId,taskId,epoch});return run;
}
/** Validate persisted invariants before writes. Corrupt state must not be silently repaired. */
export function validateRunSnapshot(run){
 assert(run&&typeof run==='object'&&!Array.isArray(run),'Corrupt run object');
 assert([2,3].includes(run.schema),'Unsupported run schema');
 assert(idOk(run.id)&&knownStatus.has(run.status),'Invalid run identity or state');
 assert(Array.isArray(run.slots)&&run.slots.length===MAX_WORKERS,'Run requires exactly five slots');
 assert(Array.isArray(run.tasks)&&run.tasks.length>=1&&run.tasks.length<=100,'Invalid tasks');
 assert(Array.isArray(run.audit)&&Array.isArray(run.seenEventIds)&&Number.isSafeInteger(run.revision)&&run.revision>=0,'Invalid event storage');
 workerLimit(run.maxConcurrent??MAX_WORKERS);
 const names=new Set(),tabIds=new Set(),sessionIds=new Set(),leased=new Set(),byId=new Map();
 for(const t of run.tasks){
  assert(idOk(t.id)&&!byId.has(t.id),'Duplicate or invalid task id');
  assert(['PENDING','RUNNING','VERIFYING','BLOCKED','OWNER_APPROVED','VERIFIED'].includes(t.status),'Invalid task status');
  assert(Array.isArray(t.resources)&&Array.isArray(t.depends_on),'Invalid task graph');
  byId.set(t.id,t);
 }
 for(const w of run.slots){
  assert(idOk(w.id)&&!names.has(w.id),'Duplicate worker');names.add(w.id);
  assert(Number.isSafeInteger(w.epoch)&&w.epoch>=0,'Invalid epoch');
  assert(['AWAITING_TAB','IDLE','WORKING','VERIFYING','RECONCILE_REQUIRED','BLOCKED','MISSING_TAB','ROTATING'].includes(w.phase),'Invalid worker phase');
  if(w.tabId!==null){assert(Number.isSafeInteger(w.tabId)&&w.tabId>0&&!tabIds.has(w.tabId),'Invalid or duplicate browser tab');tabIds.add(w.tabId);}
  if(w.taskId){assert(byId.has(w.taskId)&&!leased.has(w.taskId),'Missing or duplicated lease');leased.add(w.taskId);
   assert(!['IDLE','AWAITING_TAB'].includes(w.phase),'Task in idle slot');
   const t=byId.get(w.taskId);assert(['RUNNING','VERIFYING','BLOCKED'].includes(t.status),'Active lease has terminal task');
  } else assert(!['WORKING','VERIFYING','RECONCILE_REQUIRED','ROTATING','BLOCKED'].includes(w.phase),'Working slot without task');
 }
 for(const t of run.tasks)if(['RUNNING','VERIFYING','BLOCKED'].includes(t.status))assert(leased.has(t.id),'Unleased active task');
 for(const t of run.tasks)for(const d of t.depends_on)assert(byId.has(d)&&d!==t.id,'Unknown or self dependency');
 const visiting=new Set(),visited=new Set();
 const dfs=id=>{assert(!visiting.has(id),'Dependency cycle in snapshot');if(visited.has(id))return;visiting.add(id);for(const d of byId.get(id).depends_on)dfs(d);visiting.delete(id);visited.add(id);};
 for(const t of run.tasks)dfs(t.id);
 const busy=new Set();
 for(const t of run.tasks)if(['RUNNING','VERIFYING','BLOCKED'].includes(t.status)){
  for(const res of t.resources){assert(idOk(res),'Invalid task resource');assert(!busy.has(res),'Overlapping active resource leases');busy.add(res);}
 }
 for(const w of run.slots){
  if(w.sessionId!=null){assert(idOk(w.sessionId)&&!sessionIds.has(w.sessionId),'Invalid or duplicated conversation session');sessionIds.add(w.sessionId);}
  if(w.phase==='AWAITING_TAB')assert(w.tabId===null&&!w.taskId,'Awaiting tab has a stale binding');
  if(w.phase==='IDLE')assert(w.tabId!==null&&!w.taskId,'Idle slot must own a tab');
  if(w.taskId){const t=byId.get(w.taskId);
   if(w.phase==='VERIFYING')assert(t.status==='VERIFYING','Verifier phase/task mismatch');
   if(w.phase==='BLOCKED'||w.phase==='MISSING_TAB')assert(t.status==='BLOCKED','Blocked phase/task mismatch');
  }
  if(w.pendingSend){assert(w.taskId&&idOk(w.pendingSend.messageId)&&w.pendingSend.status==='UNKNOWN','Invalid pending send');}
  if(w.monitor?.trusted){const h=w.monitor.trusted;
   assert(Number.isSafeInteger(h.sequence)&&h.sequence>0,'Invalid heartbeat sequence');
   assert(validClock(h.lastSeenAtMs)&&validClock(h.lastProgressAtMs)&&h.lastProgressAtMs<=h.lastSeenAtMs,'Invalid heartbeat clock');
   assert(h.fingerprint===null||textOk(h.fingerprint,200),'Invalid heartbeat fingerprint');
  }
 }
 return true;
}
