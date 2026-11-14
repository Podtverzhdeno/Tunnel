/** Offline dependency/capacity analysis. Pure and deterministic; no browser APIs. */
export const DEFAULT_CONCURRENCY=5;
export function workerLimit(value){
 if(!Number.isInteger(value)||value<1||value>5)throw new Error('Concurrency must be an integer from 1 to 5');
 return value;
}
const effort=t=>t.estimatedEffort??1;
const priority=t=>t.priority??3;
export function taskRank(tasks){
 const children=new Map(tasks.map(t=>[t.id,[]]));
 for(const t of tasks)for(const d of t.depends_on??[])children.get(d).push(t.id);
 const byId=new Map(tasks.map(t=>[t.id,t]));
 const memo=new Map(),next=new Map();
 function score(id){
  if(memo.has(id))return memo.get(id);
  let best=0,child=null;
  for(const c of children.get(id)){
   const w=score(c);if(w>best){best=w;child=c;}
  }
  next.set(id,child);const total=effort(byId.get(id))+best;memo.set(id,total);return total;
 }
 for(const t of tasks)score(t.id);
 const positions=new Map(tasks.map((t,i)=>[t.id,i]));
 const ordered=[...tasks].sort((a,b)=>memo.get(b.id)-memo.get(a.id)||priority(b)-priority(a)||positions.get(a.id)-positions.get(b.id));
 const path=[];for(let id=ordered[0]?.id;id;id=next.get(id))path.push(id);
 return {weights:memo,ordered,criticalPath:path,criticalWork:path.length?memo.get(path[0]):0};
}
/** Validated plan only; call validatePlan before this for third-party input. */
export function analyzePlan(tasks,concurrency=DEFAULT_CONCURRENCY){
 workerLimit(concurrency);
 const ranked=taskRank(tasks),resourceUsers=new Map();
 for(const t of tasks)for(const r of t.resources??[])resourceUsers.set(r,(resourceUsers.get(r)??0)+1);
 const roots=tasks.filter(t=>(t.depends_on??[]).length===0);
 const occupied=new Set(),initial=[];
 for(const t of ranked.ordered){
  if(initial.length>=concurrency)break;
  if((t.depends_on??[]).length||(t.resources??[]).some(r=>occupied.has(r)))continue;
  initial.push(t.id);for(const r of t.resources??[])occupied.add(r);
 }
 const totalEffort=tasks.reduce((n,t)=>n+effort(t),0);
 return {taskCount:tasks.length,rootTasks:roots.length,dependencyEdges:tasks.reduce((n,t)=>n+(t.depends_on??[]).length,0),
  resourceLocks:resourceUsers.size,criticalPath:ranked.criticalPath,criticalWork:ranked.criticalWork,
  estimatedTotalEffort:totalEffort,optimisticMinWorkUnits:Math.max(ranked.criticalWork,Math.ceil(totalEffort/concurrency)),
  initialAssignments:initial,initialParallelism:initial.length,maxConcurrent:concurrency,
  contendedResources:[...resourceUsers].filter(([,n])=>n>1).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,8).map(([id,taskCount])=>({id,taskCount})),
  notes:['Рабочие единицы — оценки пользователя, не время и не прогноз реальной скорости.','Минимум — оптимистичная нижняя граница без учета конфликтов ресурсов и сбоев.']};
}
/** Human-readable why a worker is idle; it never causes a side effect. */
export function queueInsights(run){
 const tasks=run.tasks,byId=new Map(tasks.map(t=>[t.id,t])),maxConcurrent=workerLimit(run.maxConcurrent??5),
  occupied=run.slots.filter(s=>s.taskId!==null).length,
  busyResources=new Set(tasks.filter(t=>['RUNNING','VERIFYING','BLOCKED'].includes(t.status)).flatMap(t=>t.resources));
 const counts={READY:0,WAITING_DEPENDENCIES:0,RESOURCE_LOCKED:0,BLOCKED:0,RUNNING:0,VERIFYING:0,COMPLETED:0};
 const waiting=[];
 for(const t of tasks){
  if(['VERIFIED','OWNER_APPROVED'].includes(t.status)){counts.COMPLETED++;continue;}
  if(t.status==='BLOCKED'){counts.BLOCKED++;waiting.push({taskId:t.id,reason:'BLOCKED'});continue;}
  if(t.status==='RUNNING'||t.status==='VERIFYING'){counts[t.status]++;continue;}
  const unmet=t.depends_on.filter(id=>!['VERIFIED','OWNER_APPROVED'].includes(byId.get(id)?.status));
  if(unmet.length){counts.WAITING_DEPENDENCIES++;waiting.push({taskId:t.id,reason:'WAITING_DEPENDENCIES',dependencies:unmet});continue;}
  const locks=t.resources.filter(r=>busyResources.has(r));
  if(locks.length){counts.RESOURCE_LOCKED++;waiting.push({taskId:t.id,reason:'RESOURCE_LOCKED',resources:locks});continue;}
  counts.READY++;
 }
 let idleReason='';
 if(run.status!=='ACTIVE')idleReason=`RUN_${run.status}`;
 else if(occupied>=maxConcurrent)idleReason='CONCURRENCY_LIMIT';
 else if(counts.READY)idleReason='AWAITING_DISPATCH_OR_FREE_TAB';
 else if(counts.BLOCKED)idleReason='BLOCKED_REQUIRES_REVIEW';
 else if(counts.WAITING_DEPENDENCIES)idleReason='WAITING_DEPENDENCIES';
 else if(counts.RESOURCE_LOCKED)idleReason='RESOURCE_LOCKED';
 else idleReason='NO_PENDING_TASKS';
 const slotReasons=run.slots.map(s=>{
  let reason;
  if(s.phase==='MISSING_TAB')reason=s.taskId?'LOST_WORK_REQUIRES_RECONCILIATION':'MISSING_IDLE_TAB';
  else if(s.phase==='AWAITING_TAB')reason='TAB_NOT_PROVISIONED';
  else if(s.phase==='BLOCKED')reason='WORK_BLOCKED';
  else if(s.taskId)reason=s.phase;
  else if(run.status!=='ACTIVE')reason=`RUN_${run.status}`;
  else if(occupied>=maxConcurrent)reason='CONCURRENCY_LIMIT';
  else reason=idleReason;
  return {workerId:s.id,reason,taskId:s.taskId};
 });
 return {maxConcurrent,occupied,capacityAvailable:Math.max(0,maxConcurrent-occupied),counts,waiting,idleReason,slotReasons,
  missingTabs:run.slots.filter(s=>s.phase==='MISSING_TAB').map(s=>s.id),
  blockedTasks:tasks.filter(t=>t.status==='BLOCKED').map(t=>t.id)};
}
/** Redacted event-only observability, not a time-series throughput estimate. */
export function auditInsights(run,limit=25){
 if(!run)return {revision:0,counts:{},events:[]};
 if(!Number.isInteger(limit)||limit<1||limit>100)throw new Error('Audit limit must be 1..100');
 const counts={};for(const event of run.audit){counts[event.type]=(counts[event.type]??0)+1;}
 // Strict allowlist of structured fields; never include checkpoint/prompt/evidence/owner note.
 const keys=['revision','type','workerId','taskId','epoch','tabId','oldTabId','newTabId','retry','maxConcurrent','outcome'];
 const events=run.audit.slice(-limit).map(event=>Object.fromEntries(keys.filter(k=>Object.hasOwn(event,k)).map(k=>[k,event[k]])));
 return {revision:run.revision,counts,events,trimmed:run.audit.length>=500,latencyMeasured:false};
}

/** Decision support, not automatic capacity change; no verified transport telemetry exists. */
export function capacityAdvice(run){
 if(!run)return {proposed:5,current:5,reason:'NO_RUN',applyAutomatically:false};
 const current=workerLimit(run.maxConcurrent??5),q=queueInsights(run);
 if(run.status!=='ACTIVE')return {proposed:current,current,reason:'RUN_NOT_ACTIVE',applyAutomatically:false};
 const critical=run.slots.filter(s=>s.taskId&&s.monitor?.level==='CRITICAL').length;
 const warning=run.slots.filter(s=>s.taskId&&s.monitor?.level==='WARNING').length;
 if(critical>=2)return {proposed:Math.max(1,current-1),current,reason:'MULTIPLE_UNCONFIRMED_LEASES',critical,warning,applyAutomatically:false};
 if(q.blockedTasks.length)return {proposed:current,current,reason:'REVIEW_BLOCKED_TASKS_FIRST',critical,warning,applyAutomatically:false};
 if(q.counts.READY>=2&&q.capacityAvailable===0&&current<5&&critical===0&&warning===0)
  return {proposed:current+1,current,reason:'READY_QUEUE_CAPACITY_BOTTLENECK',critical,warning,applyAutomatically:false};
 return {proposed:current,current,reason:warning?'CHECK_WARNINGS':'HOLD',critical,warning,applyAutomatically:false};
}
