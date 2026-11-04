"use strict";
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const code=fs.readFileSync(__dirname+"/background_content.js","utf8");
const id="01234567-89ab-4cde-8901-23456789abcd";
const prompt="Тест фонового запуска. Ответь одним словом: ПРИНЯТО.";
async function simulate(mode){
  const received=[],location={
    hash:"#chat-launch-smoke=bg-"+id,pathname:"/",search:"",
    href:"https://chatgpt.com/#chat-launch-smoke=bg-"+id
  };
  let attempts=0,clicks=0,time=0,fillCalls=0;
  const editor={ownerDocument:null};
  const messages=[
    {getAttribute:k=>k==="data-message-author-role"?"user":null,textContent:prompt}
  ];
  const assistant={textContent:"ПРИНЯТО"};
  const doc={
    querySelectorAll(sel){
      if(sel.includes('[data-message-author-role="user"]'))return clicks?messages:[];
      if(sel==="[data-message-author-role]")return messages;
      if(sel.includes('[data-message-author-role="assistant"]'))return clicks && mode!=="slow_answer"?[assistant]:[];
      return [];
    },
    querySelector(){return null},
    body:{innerText:prompt}
  };
  editor.ownerDocument=doc;
  const core={
    findEditor(){return mode==="no_editor"?null:editor;},
    fillEditor(){fillCalls++;return mode!=="bad_fill";},
    detectAccountConflict(){return mode==="account_error"&&clicks>0;},
    findSendButton(){return mode==="no_button" || (mode==="resync_button" && fillCalls<2)?null:{click(){
      clicks++;location.pathname="/c/abcdef-1234";
      location.href="https://chatgpt.com/c/abcdef-1234";
    }};}
  };
  const chrome={runtime:{async sendMessage(msg){
    received.push({...msg});
    if(msg.type==="chat-smoke-get"){
      attempts++;
      if(attempts<4)return {ok:false};
      return {ok:true,prompt,completionMode:mode==="gated"};
    }
    return {ok:true};
  }}};
  const context={
    location,document:doc,chrome,ChatLaunchSmoke:core,
    history:{replaceState(){}},
    Date:class extends Date {static now(){time+=500;return time;}},
    setTimeout:(callback)=>setImmediate(callback)
  };
  context.window=context;context.top=context;context.globalThis=context;
  vm.runInNewContext(code,context);
  for(let i=0;i<300;i++){
    await new Promise(resolve=>setImmediate(resolve));
    if(received.some(x=>x.type==="chat-smoke-finished"))break;
  }
  return {received,attempts,clicks};
}
(async()=>{
  const good=await simulate("good");
  assert.equal(good.attempts,4,"ownership handoff retried but no prompt resent");
  assert.equal(good.clicks,1,"exactly one send");
  assert.equal(good.received.find(x=>x.type==="chat-smoke-finished")?.status,"done");
  assert.equal(good.received.find(x=>x.type==="chat-smoke-finished")?.reason,"assistant_confirmed");
  const resynced=await simulate("resync_button");
  assert.equal(resynced.clicks,1,"editor re-sync allows a single submit after hydration");
  const auth=await simulate("account_error");
  assert.equal(auth.clicks,1,"authentication error must not trigger second click");
  assert.equal(auth.received.find(x=>x.type==="chat-smoke-finished")?.status,"unknown");
  assert.equal(auth.received.find(x=>x.type==="chat-smoke-finished")?.reason,
    "account_context_changed");
  assert.equal(auth.received.some(x=>x.type==="chat-smoke-stage"&&
    x.stage==="auth_context_changed"),true);
  const gated=await simulate("gated");
  assert.equal(gated.clicks,1,"gated agent clicks Send at most once");
  assert.equal(gated.received.some(x=>x.type==="chat-smoke-stage"&&
    x.stage==="message_saved"&&x.url==="https://chatgpt.com/c/abcdef-1234"),true,
    "saved chat URL reaches worker for report matching");
  assert.equal(gated.received.some(x=>x.type==="chat-smoke-finished"),false,
    "gated tab never self-closes based on DOM or timeout");
  const early=await simulate("slow_answer");
  assert.equal(early.clicks,1);
  assert.equal(early.received.find(x=>x.type==="chat-smoke-finished")?.status,"submitted");
  assert.equal(early.received.find(x=>x.type==="chat-smoke-finished")?.userSeen,true);
  const noButton=await simulate("no_button");
  assert.equal(noButton.received.some(x=>x.type==="chat-smoke-stage"&&
    x.stage==="send_ui_diagnostic"&&x.reason==="button_absent"),true,
    "missing button reports bounded metadata without leaking a prompt");
  for(const [mode,reason] of [["no_editor","editor_not_found"],["bad_fill","editor_fill_failed"],
                              ["no_button","send_button_missing"]]){
    const v=await simulate(mode);
    assert.equal(v.clicks,0);
    assert.equal(v.received.find(x=>x.type==="chat-smoke-finished")?.reason,reason);
    assert.equal(v.received.find(x=>x.type==="chat-smoke-finished")?.status,"unknown");
  }
  console.log("PASS: handoff retry, editor re-sync after hydration, exactly-once send and receipts");
})().catch(e=>{console.error(e);process.exitCode=1});
