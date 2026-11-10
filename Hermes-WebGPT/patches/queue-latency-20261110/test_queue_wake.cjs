"use strict";
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const assert=require('node:assert/strict');
const source=fs.readFileSync(process.env.HERMES_BG_SOURCE||path.join(__dirname,'hermes_relay_bg.js'),'utf8');
const A='11111111-1111-4111-8111-111111111111';
const B='22222222-2222-4222-8222-222222222222';
const activeKey='hermesRelayActive_v1';
const flush=async()=>{for(let i=0;i<40;i++)await new Promise(setImmediate);};
const clone=v=>JSON.parse(JSON.stringify(v));
function harness(options={}){
 let now=0,sequence=0,handlers=[],alarms=[],timers=[];
 const effects=[],states=new Map([[A,options.server||'saved']]);
 const store={hermesRelayEnabled_v1:options.enabled!==false};
 const current={job:{id:A,lease:'lease-a',prompt:'synthetic'},windowId:7,tabId:91,
  documentId:'doc-a',state:options.local||'saved',savedUrl:'https://chatgpt.com/c/test'};
 if(!options.empty)store[activeKey]=clone(current);
 const tabs=new Map([[91,{id:91,windowId:7,active:false,status:'complete',url:current.savedUrl}]]);
 const queue=[];
 let releaseResult=null,releaseClaim=null;
 const chrome={runtime:{id:'test',getURL:n=>'chrome-extension://test/'+n,
  onMessage:{addListener:f=>handlers.push(f)}},
  storage:{local:{get:async key=>({[key]:clone(store[key]??null)}),set:async values=>{
   if(options.archiveFails&&Object.keys(values).some(k=>k.startsWith('hermesRelayTerminal_v1_')))
    throw Error('synthetic storage failure');
   Object.assign(store,clone(values));
  }}},
  windows:{getLastFocused:async()=>({id:7,type:'normal'}),get:async()=>({id:7,type:'normal'})},
  tabs:{get:async id=>clone(tabs.get(id)),create:async opts=>{
   effects.push({type:'tab',at:now,url:opts.url});
   const tab={id:100+effects.filter(x=>x.type==='tab').length,status:'loading',...opts};
   tabs.set(tab.id,tab);return clone(tab);
  },remove:async id=>{effects.push({type:'remove',id});tabs.delete(id);},onUpdated:{addListener(){}}},
  scripting:{executeScript:async()=>{throw Error('Unexpected injection in queue-only fixture');}},
  alarms:{create:(name,opts)=>effects.push({type:'alarm_config',name,...opts}),
   onAlarm:{addListener:f=>alarms.push(f)}}};
 const fetch=async(url,optionsFetch={})=>{
  if(url.startsWith('chrome-extension:'))return {ok:true,json:async()=>({endpoint:'http://127.0.0.1:18765',key:'x'.repeat(40)})};
  const route=new URL(url).pathname,body=JSON.parse(optionsFetch.body||'{}');
  effects.push({type:'api',route,action:body.action,id:body.id,at:now});
  let result={ok:true};
  if(route==='/bridge/claim'){
   const id=queue.shift()||null;
   result={ok:true,job:id?{id,lease:'lease-b',prompt:'synthetic-next'}:null};
   if(id)states.set(id,'claimed');
   if(options.holdClaim)await new Promise(resolve=>{releaseClaim=resolve;});
  }
  if(route==='/bridge/event'){
   if(body.action==='inspect')result={ok:true,state:states.get(body.id),tab:body.tab};
   if(body.action==='result'){
    if(options.holdResult)await new Promise(resolve=>{releaseResult=resolve;});
    if(options.rejectResult)return {ok:false,json:async()=>({})};
    states.set(body.id,'done');result={ok:true,state:'done'};
   }
   if(body.action==='blocked'){
    const old=states.get(body.id);
    const state=['done','blocked','uncertain'].includes(old)?old:old==='saved'?'uncertain':'blocked';
    states.set(body.id,state);result={ok:true,state};
   }
  }
  return {ok:true,json:async()=>result};
 };
 const clock={now:()=>now};
 const load=()=>vm.runInNewContext(source,{chrome,fetch,URL,AbortSignal,Date:clock,
  crypto:{randomUUID:()=>A},setTimeout:(fn,delay)=>{
   timers.push({fn,at:now+delay,seq:++sequence});return sequence;
  }});
 load();
 const call=(message,sender={id:'test',url:'chrome-extension://test/popup.html'})=>
  new Promise(resolve=>handlers.at(-1)(message,sender,resolve));
 const finish=(extra={})=>call({type:'hermes-relay-finished',id:A,status:'done',
  userSeen:true,assistantSeen:true,url:current.savedUrl,responseText:'synthetic-reply',...extra},
  {id:'test',tab:{id:91},frameId:0,documentId:'doc-a'});
 const advance=async until=>{
  while(true){
   timers.sort((a,b)=>a.at-b.at||a.seq-b.seq);
   if(!timers.length||timers[0].at>until)break;
   const timer=timers.shift();now=timer.at;timer.fn();await flush();
  }
  now=until;await flush();
 };
 return {store,states,effects,tabs,queue,finish,call,advance,
  restart(){timers=[];handlers=[];alarms=[];load();},
  release:()=>releaseResult?.(),timerCount:()=>timers.length,
  releaseClaim:()=>releaseClaim?.(),
  poll:async()=>{alarms.at(-1)({name:'hermes-relay-poll'});await flush();}};
}
const cases=[];const test=(name,run)=>cases.push([name,run]);
const claims=h=>h.effects.filter(x=>x.route==='/bridge/claim');
const results=h=>h.effects.filter(x=>x.action==='result');
test('ACKed completion claims queued following request without waiting for alarm',async()=>{
 const h=harness();h.queue.push(B);assert.equal((await h.finish()).ok,true);
 await h.advance(0);assert.equal(h.store[activeKey].job.id,B);assert.equal(claims(h).length,1);
});
test('request arriving after first empty poll is claimed by bounded warm check',async()=>{
 const h=harness();await h.finish();await h.advance(0);h.queue.push(B);await h.advance(1000);
 assert.equal(h.store[activeKey].job.id,B);assert.equal(claims(h).at(-1).at,1000);
});
test('empty queue stops after four checks and preserves 30 second fallback',async()=>{
 const h=harness();await h.finish();await h.advance(60000);
 assert.equal(claims(h).length,4);assert.equal(h.timerCount(),0);
 assert.equal(h.effects.find(x=>x.type==='alarm_config').periodInMinutes,0.5);
});
test('enable catches a request arriving after the initial empty check',async()=>{
 const h=harness({empty:true,enabled:false});await h.call({type:'hermes-relay-enable'});
 await h.advance(0);h.queue.push(B);await h.advance(3000);
 assert.equal(h.store[activeKey].job.id,B);assert.equal(claims(h).length,2);
});
test('Disable invalidates all pending warm callbacks',async()=>{
 const h=harness();await h.finish();await h.call({type:'hermes-relay-disable'});h.queue.push(B);
 await h.advance(10000);assert.equal(claims(h).length,0);assert.equal(h.queue.length,1);
});
test('disabled transport does not claim even after a late valid completion',async()=>{
 const h=harness({enabled:false});h.queue.push(B);await h.finish();await h.advance(10000);
 assert.equal(claims(h).length,0);
});
test('Disable during a claim ACK fences that job without creating a tab',async()=>{
 const h=harness({empty:true,holdClaim:true});h.queue.push(B);await h.poll();
 await h.call({type:'hermes-relay-disable'});h.releaseClaim();await flush();
 assert.equal(h.effects.filter(x=>x.type==='tab').length,0);
 assert.equal(h.store[activeKey].job.id,B);assert.equal(h.store[activeKey].state,'blocked');
 assert.equal(h.effects.filter(x=>x.action==='intent').length,0);
});
test('unknown Send never starts a completion warm burst',async()=>{
 const h=harness();await h.finish({status:'unknown',reason:'uncertain_send'});
 await h.advance(10000);assert.equal(h.store[activeKey].state,'uncertain');assert.equal(claims(h).length,0);
});
test('unaccepted result keeps active ownership and schedules no follow-up',async()=>{
 const h=harness({rejectResult:true});assert.equal((await h.finish()).ok,false);
 await h.advance(10000);assert.equal(h.store[activeKey].job.id,A);assert.equal(claims(h).length,0);
});
test('terminal archive permits a different queued request in the same alarm',async()=>{
 const h=harness({server:'uncertain',local:'uncertain'});h.queue.push(B);await h.poll();
 assert.equal(h.store['hermesRelayTerminal_v1_'+A].noReplay,true);
 assert.equal(h.store[activeKey].job.id,B);assert.equal(claims(h).length,1);
});
test('nonterminal server state never releases the active slot',async()=>{
 const h=harness({server:'saved'});h.queue.push(B);await h.poll();
 assert.equal(h.store[activeKey].job.id,A);assert.equal(claims(h).length,0);
});
test('failed terminal archive prevents a new claim',async()=>{
 const h=harness({server:'blocked',local:'blocked',archiveFails:true});h.queue.push(B);await h.poll();
 assert.equal(h.store[activeKey].job.id,A);assert.equal(claims(h).length,0);
});
test('archived request returned by server is refused without creating a tab',async()=>{
 const h=harness({server:'blocked',local:'blocked'});h.queue.push(A);await h.poll();
 assert.equal(h.effects.filter(x=>x.type==='tab').length,0);
 assert.equal(h.store['hermesRelayTerminal_v1_'+A].noReplay,true);
});
test('simultaneous result callbacks produce one result POST and one close',async()=>{
 const h=harness({holdResult:true});const first=h.finish();await flush();
 const second=await h.finish();assert.equal(second.ok,false);assert.equal(second.reason,'result_in_progress');
 h.release();assert.equal((await first).ok,true);assert.equal(results(h).length,1);
 assert.equal(h.effects.filter(x=>x.type==='remove').length,1);
});
test('result finishing prevents a concurrent alarm from claiming',async()=>{
 const h=harness({holdResult:true});h.queue.push(B);const first=h.finish();await flush();
 await h.poll();assert.equal(claims(h).length,0);h.release();await first;await h.advance(0);
 assert.equal(h.store[activeKey].job.id,B);
});
test('late completion cannot erase a replaced active job',async()=>{
 const h=harness({holdResult:true});const first=h.finish();await flush();
 h.store[activeKey]={job:{id:B,lease:'new-lease'},state:'created'};h.release();
 assert.equal((await first).ok,false);assert.equal(h.store[activeKey].job.id,B);
 assert.equal(h.effects.filter(x=>x.type==='remove').length,0);
});
test('active user tab is preserved after completion',async()=>{
 const h=harness();h.tabs.get(91).active=true;await h.finish();
 assert.equal(h.effects.filter(x=>x.type==='remove').length,0);
});
test('warm checks do not inspect or alter an already claimed following job',async()=>{
 const h=harness();h.queue.push(B);await h.finish();await h.advance(10000);
 assert.equal(claims(h).length,1);assert.equal(h.effects.filter(x=>x.action==='inspect'&&x.id===B).length,0);
});
test('worker restart relies on alarm and keeps durable non-replay fence',async()=>{
 const h=harness({server:'uncertain',local:'uncertain'});h.restart();h.queue.push(B);await h.poll();
 assert.equal(h.store['hermesRelayTerminal_v1_'+A].noReplay,true);assert.equal(h.store[activeKey].job.id,B);
});
test('status reports the loaded queue-wake protocol',async()=>{
 const h=harness({empty:true});assert.equal((await h.call({type:'hermes-relay-status'})).queueWakeProtocol,1);
});
(async()=>{
 const outcomes=[];
 for(const [name,run] of cases){let timeout;try{
  await Promise.race([run(),new Promise((_,reject)=>{
   timeout=setTimeout(()=>reject(Error('test_timeout')),2000);
  })]);outcomes.push({name,pass:true});
 }catch(error){outcomes.push({name,pass:false,error:error.message});}
 finally{clearTimeout(timeout);}}
 const report={ok:outcomes.every(x=>x.pass),passed:outcomes.filter(x=>x.pass).length,
 failed:outcomes.filter(x=>!x.pass).length,environment:'Node VM, synthetic Chrome and HTTP',
 real_model_sends:0,results:outcomes};
 console.log(JSON.stringify(report));process.exitCode=report.ok?0:1;
})();
