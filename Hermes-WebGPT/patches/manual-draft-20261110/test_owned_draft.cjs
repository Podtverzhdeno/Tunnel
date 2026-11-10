"use strict";
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync(process.env.HERMES_BG_SOURCE||__dirname+'/hermes_relay_bg.js','utf8');
const begin=source.indexOf('function makeHermesOwnedDraftRecovery('),end=source.indexOf('\nlet polling=',begin);
if(begin<0||end<0)throw Error('draft_recovery_factory_missing');
const make=new Function(source.slice(begin,end)+';return makeHermesOwnedDraftRecovery;')();
const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
const ACTIVE='hermesRelayActive_v1',TERMINAL='hermesRelayTerminal_v1_'+A;
const prompt='Synthetic owned draft {"request_id":"'+A+'"}\n[HR-'+A+']';
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const clone=x=>JSON.parse(JSON.stringify(x));
function harness(options={}){
 const task={job:{id:B,lease:'synthetic-new-lease'},windowId:7,tabId:91,documentId:'new-doc',state:'preparing'};
 const record={schema:'hermes-terminal-v1',noReplay:true,server:{state:'blocked'},retiredAt:1,
  task:{job:{id:A,lease:'synthetic-old-lease',prompt},windowId:7,tabId:90,documentId:'old-doc',state:'blocked',reason:'send_button_missing'}};
 const store={[ACTIVE]:clone(task),[TERMINAL]:clone(record)},writes=[];
 let inspectCount=0,ownedCount=0,digests=0;
 const storage={get:async key=>{
  if(options.lostBackupRead&&String(key).startsWith('hermesDraftQuarantine_'))return {};
  return key===null?clone(store):{[key]:clone(store[key]??null)};
 },set:async data=>{
  writes.push(clone(data));
  if(options.failBackup)throw Error('synthetic storage failure');
  Object.assign(store,clone(data));
  if(options.activeChangesOnBackup)store[ACTIVE]={...task,job:{id:A,lease:'changed'}};
 }};
 const inspect=async()=>{
  inspectCount++;
  return {ok:true,state:options.afterBackupIntent&&inspectCount>1?'intent':'claimed',tab:null};
 };
 const owned=async()=>{ownedCount++;return !options.foreground&&!(options.foregroundOnLastCheck&&ownedCount>=4);};
 const digest=async text=>{
  digests++;
  if(options.activeChangesDuringHash)store[ACTIVE]={...task,job:{id:A,lease:'changed'}};
  return sha(text);
 };
 const recover=make(storage,inspect,owned,digest,async()=>options.disabled!==true);
 return {task,record,store,writes,recover:()=>recover(task,sha(prompt),prompt.length),
  direct:recover,inspects:()=>inspectCount,digests:()=>digests};
}
const cases=[];const test=(name,run)=>cases.push([name,run]);
test('exact known pre-send draft is durably quarantined before authorization',async()=>{
 const h=harness(),old=JSON.stringify(h.store[TERMINAL]);const result=await h.recover();
 assert.equal(result.ok,true);assert.equal(result.quarantined,true);assert.equal(h.writes.length,1);
 const backup=h.store['hermesDraftQuarantine_v1_'+B];assert.equal(backup.prompt,prompt);
 assert.equal(backup.noReplay,true);assert.equal(backup.sourceJobId,A);assert.equal(backup.targetJobId,B);
 assert.equal(JSON.stringify(h.store[TERMINAL]),old);assert.equal(h.inspects(),2);
});
test('unrecognized manual text is never backed up or authorized for replacement',async()=>{
 const h=harness();const result=await h.direct(h.task,sha('user private draft'),18);
 assert.equal(result.ok,false);assert.equal(h.writes.length,0);
});
for(const state of ['uncertain','done'])test('terminal '+state+' cannot authorize replacement',async()=>{
 const h=harness();h.store[TERMINAL].server.state=state;
 assert.equal((await h.recover()).ok,false);assert.equal(h.writes.length,0);
});
test('archive without a noReplay fence is rejected',async()=>{
 const h=harness();h.store[TERMINAL].noReplay=false;assert.equal((await h.recover()).ok,false);
});
test('a server-bound tab proves prior intent and forbids replacement',async()=>{
 const h=harness();h.store[TERMINAL].server.tab=90;
 assert.equal((await h.recover()).ok,false);assert.equal(h.writes.length,0);
});
test('old manual_input record is not evidence that its prompt was ever typed',async()=>{
 const h=harness();h.store[TERMINAL].task.reason='manual_input';assert.equal((await h.recover()).ok,false);
});
test('different window cannot authorize a recovery',async()=>{
 const h=harness();h.store[TERMINAL].task.windowId=8;assert.equal((await h.recover()).ok,false);
});
test('missing document identity cannot authorize a recovery',async()=>{
 const h=harness();delete h.task.documentId;delete h.store[ACTIVE].documentId;
 assert.equal((await h.recover()).ok,false);assert.equal(h.writes.length,0);
});
test('new intent is never eligible for composer replacement',async()=>{
 const h=harness();h.task.state=h.store[ACTIVE].state='intent';assert.equal((await h.recover()).ok,false);
});
test('a disabled worker cannot recover a draft',async()=>{
 const h=harness({disabled:true});assert.equal((await h.recover()).ok,false);assert.equal(h.inspects(),0);
});
test('a foreground user tab cannot recover a draft',async()=>{
 const h=harness({foreground:true});assert.equal((await h.recover()).ok,false);assert.equal(h.writes.length,0);
});
test('archive without its exact old nonce footer is rejected',async()=>{
 const h=harness();h.store[TERMINAL].task.job.prompt='x'.repeat(prompt.length);assert.equal((await h.recover()).ok,false);
});
test('archive without its request envelope is rejected',async()=>{
 const h=harness();h.store[TERMINAL].task.job.prompt=prompt.replace('request_id','request_ix');
 assert.equal((await h.recover()).ok,false);
});
test('missing archive cannot authorize replacement',async()=>{
 const h=harness();delete h.store[TERMINAL];assert.equal((await h.recover()).ok,false);
});
test('wrong archive schema is rejected',async()=>{
 const h=harness();h.store[TERMINAL].schema='different';assert.equal((await h.recover()).ok,false);
});
test('failed durable backup never authorizes replacement',async()=>{
 const h=harness({failBackup:true});await assert.rejects(h.recover(),/storage failure/);
});
test('lost backup readback never authorizes replacement',async()=>{
 const h=harness({lostBackupRead:true});assert.equal((await h.recover()).ok,false);
});
test('active-job change while hashing aborts without overwriting the new job',async()=>{
 const h=harness({activeChangesDuringHash:true});assert.equal((await h.recover()).ok,false);
 assert.equal(h.writes.length,0);assert.equal(h.store[ACTIVE].job.id,A);
});
test('active-job change while saving the backup preserves the replacement job',async()=>{
 const h=harness({activeChangesOnBackup:true});assert.equal((await h.recover()).ok,false);
 assert.equal(h.store[ACTIVE].job.id,A);
});
test('server moving to intent after backup prevents authorization',async()=>{
 const h=harness({afterBackupIntent:true});assert.equal((await h.recover()).ok,false);
});
test('activation after the final inspect is detected before authorization',async()=>{
 const h=harness({foregroundOnLastCheck:true});assert.equal((await h.recover()).ok,false);
});
test('an existing quarantine receipt is never blindly reused',async()=>{
 const h=harness();assert.equal((await h.recover()).ok,true);assert.equal((await h.recover()).ok,false);
 assert.equal(h.writes.length,1);
});
test('length or hash format mismatch is rejected without archive writes',async()=>{
 const h=harness();assert.equal((await h.direct(h.task,'not-a-sha',prompt.length)).ok,false);
 assert.equal((await h.direct(h.task,sha(prompt),prompt.length+1)).ok,false);assert.equal(h.writes.length,0);
});
test('same-job records cannot authorize a replacement',async()=>{
 const h=harness();h.store[TERMINAL].task.job.id=B;assert.equal((await h.recover()).ok,false);
});
(async()=>{const results=[];for(const[name,run]of cases){try{await run();results.push({name,pass:true});}
 catch(error){results.push({name,pass:false,error:error.message});}}
 const failed=results.filter(x=>!x.pass).length;
 console.log(JSON.stringify({ok:failed===0,passed:results.length-failed,failed,results,real_sends:0}));process.exitCode=failed?1:0;
})();
