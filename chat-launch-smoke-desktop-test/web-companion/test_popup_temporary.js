/* Popup-only Temporary Chat action; isolated fake browser, no network. */
"use strict";
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const js=fs.readFileSync(__dirname+"/popup.js","utf8");
const elements=new Map();
for(const id of ["state","circuit","resume","openTemporary","temporaryState"]){
  elements.set(id,{id,disabled:false,textContent:"",style:{},
    addEventListener(type,handler){this[type+"Handler"]=handler;}});
}
const sent=[];
const sandbox={
  document:{getElementById:id=>elements.get(id)},
  chrome:{
    storage:{local:{get:async()=>({})},session:{get:async()=>({})}},
    runtime:{sendMessage:async msg=>{
      sent.push(msg);
      return {ok:true,temporary_ui_verified:false};
    }}
  },
  Date
};
vm.runInNewContext(js,sandbox);
(async()=>{
  const button=elements.get("openTemporary");
  assert.equal(typeof button.clickHandler,"function");
  await button.clickHandler();
  assert.equal(sent.length,1);
  assert.equal(sent[0].type,"chat-smoke-open-temporary");
  assert.equal(button.disabled,false);
  assert.match(elements.get("temporaryState").textContent,/Вкладка открыта/);
  assert.match(elements.get("temporaryState").textContent,/Hermes не отправлял/);
  console.log("PASS: popup exact manual Temporary Chat action, no model Send");
})().catch(err=>{console.error(err);process.exitCode=1;});
