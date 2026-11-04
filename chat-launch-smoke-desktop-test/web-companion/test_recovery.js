/* Offline regression: orphaned agent tabs must be reattached only after
 * exact saved-message proof and server active-job confirmation.
 * No browser, network, account or real report is touched.
 */
"use strict";
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const source=fs.readFileSync(__dirname+"/background.js","utf8");
const idAt=i=>"01234567-89ab-4cde-8901-"+String(i).padStart(12,"0");
const urlAt=i=>"https://chatgpt.com/c/agent-"+i;
const textFor=id=>"Ты независимый исследователь Completion-Gated Runtime. "+
  "Подключи Local Workspace MCP: list_workspaces alias projects. "+
  "Запиши ровно один новый отчёт в chat-launch-smoke-desktop-test/agent-reports/completion-"+
  id+".md. Job ID: "+id+", затем заголовки ## Outcome и ## Evidence.";

async function setup({tabs=[],eligibility=[],acks={},paused=false,initialLocal={},closure={}}={}){
  const tabMap=new Map(tabs.map(t=>[t.id,{...t}]));
  const stored=paused?{chatSmokeCircuit:{failures:2,paused:true}}:{};
  const local={...initialLocal},events={},logs=[],results=[],scans=[];
  const allowed=new Set(eligibility);
  const ackMap=new Map(Object.entries(acks));
  const chrome={
    runtime:{getURL:x=>"chrome-extension://test/"+x,
      onInstalled:{addListener:f=>events.installed=f},
      onStartup:{addListener:f=>events.startup=f},
      onMessage:{addListener:f=>events.message=f}},
    storage:{session:{
      get:async k=>k===null?{...stored}:{[k]:stored[k]},
      set:async x=>Object.assign(stored,x),
      remove:async k=>{delete stored[k];}
    },local:{
      get:async k=>k===null?{...local}:typeof k==="string"?{[k]:local[k]}:Object.fromEntries(k.map(x=>[x,local[x]])),
      set:async x=>Object.assign(local,x),
       remove:async k=>{delete local[k];}
    }},
    windows:{getAll:async()=>[{id:10}]},
    alarms:{create(){},onAlarm:{addListener:f=>events.alarm=f}},
    tabs:{
      query:async()=>[...tabMap.values()],
      create:async()=>{throw Error("No new tabs permitted during recovery tests")},
      get:async id=>tabMap.get(id)||null,
      remove:async id=>{logs.push({stage:"closed",id});tabMap.delete(id)},
      onRemoved:{addListener:f=>events.removed=f}
    },
    scripting:{executeScript:async({target,func,args=[]})=>{
      const tab=tabMap.get(target.tabId);
      if(!tab)throw Error("tab_missing");
      const u=new URL(tab.url);
      const env={location:{href:tab.url,origin:u.origin,pathname:u.pathname},document:{
        querySelectorAll:()=>tab.messages.map(x=>({textContent:x})),
        querySelector:()=>tab.composerText?{textContent:tab.composerText}:null,
        body:{innerText:tab.visibleBody??tab.messages.join(" ")}
      }};
      const probe=vm.runInNewContext("("+func.toString()+")",env);
      return[{result:probe(...args)}];
    }}
  };
  const fetch=async(u,options={})=>{
    if(u.endsWith("/bridge-config.json"))return{ok:true,json:async()=>({
      endpoint:"http://127.0.0.1:18763",key:"a".repeat(40)})};
    if(u.includes("/v1/recover?id=")){
      const id=new URL(u).searchParams.get("id");
      return{ok:true,json:async()=>({eligible:allowed.has(id),reason:"test"})};
    }
    if(u.includes("/v1/close-eligible?id=")){
      const id=new URL(u).searchParams.get("id");
      return{ok:true,json:async()=>({
        eligible:Object.hasOwn(closure,id),reason:"test",
        ...(Object.hasOwn(closure,id)?{url:closure[id]}:{})
      })};
    }
    if(u.includes("/v1/ack?id=")){
      const id=new URL(u).searchParams.get("id");
      return{ok:true,json:async()=>({ack:ackMap.get(id)||null,pending:"report_not_found"})};
    }
    if(u.endsWith("/v1/recovery-scan")){
      scans.push(JSON.parse(options.body));
      return{ok:true,json:async()=>({ok:true})};
    }
    if(u.endsWith("/v1/next"))return{ok:true,json:async()=>({job:null})};
    if(u.endsWith("/v1/result")){
      results.push(JSON.parse(options.body));
      return{ok:true,json:async()=>({status:"done",ok:true})};
    }
    if(u.endsWith("/v1/event")||u.endsWith("/v1/heartbeat"))
      return{ok:true,json:async()=>({ok:true})};
    throw Error("unexpected request "+u);
  };
  const context={chrome,fetch,console:{warn(){}},Date,Promise,
    setTimeout:fn=>setImmediate(fn)};
  vm.runInNewContext(source,context);
  for(let i=0;i<135;i++)await new Promise(r=>setImmediate(r));
  return{events,tabMap,stored,local,logs,results,ackMap,allowed,scans};
}
const ack=id=>({status:"REPORT_ACKED",job_id:id,
  trust_level:"advisory_artifact_integrity_only",ack_id:"a".repeat(32)});
(async()=>{
  const id=idAt(1),other=idAt(2);
  const happy=await setup({tabs:[
    {id:300,url:urlAt(1),messages:[textFor(id)]},
    {id:301,url:urlAt(2),messages:["User's ordinary discussion of a Job ID: "+other]},
    {id:302,url:urlAt(3),messages:[textFor(other)]},
  ],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(happy.tabMap.has(300),false,"only bound agent tab is closed");
  assert.equal(happy.tabMap.has(301),true,"ordinary user tab untouched");
  assert.equal(happy.tabMap.has(302),true,"unverified job tab untouched");
  assert.equal(happy.results.length,1);
  assert.equal(happy.results[0].id,id);
  assert.equal(happy.results[0].ack_id,"a".repeat(32));
  assert.equal(happy.results[0].url,urlAt(1));
  assert.equal(happy.logs.filter(x=>x.stage==="closed").length,1);
  assert.equal(happy.scans[0].chat_tabs,3);
  assert.equal(happy.scans[0].other_chatgpt_tabs,0);
  assert.equal(happy.scans[0].non_chatgpt_tabs,0);
  assert.equal(happy.scans[0].identified,2);
  assert.equal(happy.scans[0].recovered,1);
  assert.equal(happy.scans[0].recovered_dom_only,1);
  assert.equal(happy.scans[0].restored_from_local,0);
  assert.equal(happy.local["chatSmokeOwned_"+id],undefined,
    "a completed bound job releases its persistent ownership marker");

  const noReport=await setup({tabs:[{id:303,url:urlAt(4),messages:[textFor(id)]}],
    eligibility:[id]});
  assert.equal(noReport.tabMap.has(303),true,"no ACK means no closure");
  noReport.ackMap.set(id,ack(id));
  noReport.events.alarm({name:"chat-smoke-queue"});
  for(let i=0;i<135;i++)await new Promise(r=>setImmediate(r));
  assert.equal(noReport.tabMap.has(303),false,"later report ACK closes recovered tab");
  assert.equal(noReport.results.length,1);

  const dormant=await setup({tabs:[{id:312,url:urlAt(12),messages:[textFor(id)],discarded:true}],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(dormant.tabMap.has(312),true,"discarded tab stays open");
  assert.equal(dormant.scans[0].discarded,1);
  assert.equal(dormant.scans[0].attempted,0);
  // An agent prompt might survive in a ChatGPT root page after navigation.
  // This diagnostic is recognized but MUST NOT be auto-closed or marked done
  // without a current exact saved-conversation URL.
  const unsaved=await setup({tabs:[
    {id:313,url:"https://chatgpt.com/",messages:[textFor(id)]},
    {id:314,url:"chrome://extensions",messages:[]},
    {id:315,url:"https://chatgpt.com/c/test-valid",messages:[]}
  ],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(unsaved.tabMap.has(313),true,"unsaved route preserved");
  assert.equal(unsaved.tabMap.has(314),true,"browser settings untouched");
  assert.equal(unsaved.results.length,0);
  assert.equal(unsaved.scans[0].chat_tabs,1);
  assert.equal(unsaved.scans[0].other_chatgpt_tabs,1);
  assert.equal(unsaved.scans[0].non_chatgpt_tabs,1);
  assert.equal(unsaved.scans[0].unsaved_identified,1);
  assert.equal(unsaved.scans[0].recovered,0);

  const wrong=await setup({tabs:[{id:304,url:urlAt(5),messages:[textFor(id)]}],
    eligibility:[id],acks:{[id]:ack(other)}});
  assert.equal(wrong.tabMap.has(304),true,"cross-job ACK must not close");
  assert.equal(wrong.results.length,0);

  const doubles=await setup({tabs:[
    {id:305,url:urlAt(6),messages:[textFor(id)]},
    {id:306,url:urlAt(7),messages:[textFor(id)]}
  ],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(doubles.tabMap.size,2,"ambiguous duplicate tabs stay open");
  assert.equal(doubles.results.length,0);

  const untouched=await setup({tabs:[
    {id:307,url:urlAt(8),messages:["I copied: "+textFor(id).replace(
      "Ты независимый исследователь Completion-Gated Runtime.","Different prompt.")]}
  ],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(untouched.tabMap.size,1,"copied/incomplete markers are not proof");

  const parked=await setup({tabs:[{id:308,url:urlAt(9),messages:[textFor(id)]}],
    eligibility:[id],acks:{[id]:ack(id)},paused:true});
  assert.equal(parked.tabMap.size,0,
    "verified old work can complete even when admissions are paused");
  assert.equal(parked.results.length,1);

  // Browser restart can reassign numeric tab IDs. Match durable URL and
  // unique DOM marker rather than blindly trusting the old tabId.
  const durable={schema:1,id,tabId:88,savedUrl:urlAt(1),started:10000,
    needsReconciliation:false};
  const restarted=await setup({tabs:[
    {id:401,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(restarted.tabMap.has(401),false,
    "the valid unique owned chat can be recovered after a browser restart");
  assert.equal(restarted.scans[0].recovered,1);
  assert.equal(restarted.scans[0].stored_owned,1);
  assert.equal(restarted.scans[0].restored_from_local,1);
  const restoredPending=await setup({tabs:[
    {id:402,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},eligibility:[id]});
  const getResponse=await new Promise(resolve=>restoredPending.events.message({
    type:"chat-smoke-get",id
  },{tab:{id:402}},resolve));
  assert.equal(getResponse.ok,false,
    "rehydrated agent tab must not receive a prompt to resend");
  assert.equal(getResponse.reason,"already_sent");
  assert.equal(restoredPending.tabMap.has(402),true,
    "no report ACK leaves recovered tab untouched");
  const bounded=await setup({tabs:[
    {id:407,url:urlAt(1),messages:[],
      visibleBody:textFor(id).slice(0,56)}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(bounded.results.length,1,
    "durable exact URL plus a saved visible prompt head recovers missing DOM attributes");
  assert.equal(bounded.tabMap.has(407),false,
    "bounded recovery closes only uniquely proven old task tab");

  const copied=await setup({tabs:[
    {id:408,url:urlAt(1),messages:[],
      visibleBody:textFor(id).slice(0,56)}
  ],eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(copied.tabMap.has(408),true,
    "visible common prefix without a prior durable binding is insufficient");
  assert.equal(copied.scans[0].identified,0);

  const unsent=await setup({tabs:[
    {id:409,url:urlAt(1),messages:[],
      visibleBody:textFor(id).slice(0,56),
      composerText:textFor(id)}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(unsent.tabMap.has(409),true,
    "prompt still in composer must not prove a saved user message");

  const wrongBodyId=await setup({tabs:[
    {id:410,url:urlAt(1),messages:[],
      visibleBody:textFor(id).slice(0,56)+" Job ID: "+other}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(wrongBodyId.tabMap.has(410),true,
    "a conflicting visible UUID must reject the bounded fallback");

  const alreadyDone=await setup({tabs:[
    {id:420,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    closure:{[id]:urlAt(1)}});
  assert.equal(alreadyDone.scans[0].recovered,1);
  assert.equal(alreadyDone.scans[0].restored_from_local,1);
  assert.equal(alreadyDone.tabMap.has(420),false,
    "receipt already persisted: close verified tab without a second receipt");
  assert.equal(alreadyDone.results.length,0,
    "close-only path must not resend an already accepted result");
  assert.equal(alreadyDone.local["chatSmokeOwned_"+id],undefined);

  const wrongDoneUrl=await setup({tabs:[
    {id:421,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    closure:{[id]:urlAt(2)}});
  assert.equal(wrongDoneUrl.tabMap.has(421),true,
    "server DONE for another conversation cannot authorize closure");
  assert.equal(wrongDoneUrl.scans[0].recovered,0);

  const noReceiptProof=await setup({tabs:[
    {id:422,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable}});
  assert.equal(noReceiptProof.tabMap.has(422),true,
    "a local ownership record without verified server DONE is insufficient");

  const noPriorOwner=await setup({tabs:[
    {id:423,url:urlAt(1),messages:[textFor(id)]}
  ],closure:{[id]:urlAt(1)}});
  assert.equal(noPriorOwner.tabMap.has(423),true,
    "a DONE receipt alone does not authorize closing arbitrary chats");
  assert.equal(noPriorOwner.results.length,0);

  const switched=await setup({tabs:[
    {id:401,url:urlAt(2),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(switched.tabMap.has(401),true,
    "persisted binding must not be reassigned to a different conversation");
  assert.equal(switched.scans[0].recovered,0);
  assert.equal(switched.scans[0].stored_conflict,1);
  assert.equal(switched.results.length,0);
  const disputed=await setup({tabs:[
    {id:88,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:{...durable,needsReconciliation:true}},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(disputed.tabMap.has(88),true,
    "an explicitly disputed binding must not be silently revived");
  const corrupted=await setup({tabs:[
    {id:88,url:urlAt(1),messages:[textFor(id)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:{...durable,id:other}},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(corrupted.tabMap.has(88),true,
    "invalid persistent metadata must fail closed instead of DOM fallback");
  assert.equal(corrupted.scans[0].stored_invalid,1);
  assert.equal(corrupted.scans[0].stored_conflict,1);
  const wrongIdentity=await setup({tabs:[
    {id:88,url:urlAt(1),messages:[textFor(other)]}
  ],initialLocal:{["chatSmokeOwned_"+id]:durable},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(wrongIdentity.tabMap.has(88),true,
    "reused tab ID and URL alone never establish ownership");
  const preSend=await setup({tabs:[
    {id:88,url:"https://chatgpt.com/",messages:[]}
  ],initialLocal:{["chatSmokeOwned_"+id]:{...durable,savedUrl:""}},
    eligibility:[id],acks:{[id]:ack(id)}});
  assert.equal(preSend.tabMap.has(88),true,
    "a durable pre-send marker must not cause replay or closure");
  console.log("PASS: durable restart recovery with unique DOM proof, no URL switch,"+
    " no disputed ownership, no replays, no false closure");
})().catch(e=>{console.error(e);process.exitCode=1});
