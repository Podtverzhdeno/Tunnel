/* Single temporary ChatGPT tab. Close ~5s after the posted user message is
 * visible in a persisted conversation. A "submitted" receipt does NOT claim
 * the assistant finished (closing early may interrupt generation).
 */
(()=>{
  "use strict";
  const match=location.hash.match(/^#chat-launch-smoke=bg-([0-9a-f-]{36})$/);
  if(!match||window.top!==window)return;
  const jobId=match[1],core=globalThis.ChatLaunchSmoke;
  history.replaceState(history.state,"",location.pathname+location.search);
  if(!core)return;
  const nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const normalize=text=>String(text||"").replace(/\s+/g," ").trim();
  const savedChat=()=>/^\/c\/[0-9a-z-]+(?:\/|$)/.test(location.pathname);
  async function stage(value,reason=""){
    try{await chrome.runtime.sendMessage({
      type:"chat-smoke-stage",id:jobId,stage:value,reason,
      url: /^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+/.test(location.href)?
        location.href:""
    });}catch(_){}
  }
  async function waitFor(fn,timeout){
    const deadline=Date.now()+timeout;
    while(Date.now()<deadline){
      const val=fn();if(val)return val;
      await nap(300);
    }
    return null;
  }
  async function report(status,reason,{assistantSeen=false,userSeen=false,responseText=""}={}){
    try{
      await chrome.runtime.sendMessage({
        type:"chat-smoke-finished",id:jobId,status,reason,assistantSeen,userSeen,
        responseText:status==="done"?String(responseText||"").slice(0,8000):"",
        url:/^https:\/\/chatgpt\.com\/c\/[0-9a-z-]+/.test(location.href)?location.href:""
      });
    }catch(_){}
  }
  const accountConflict=()=>typeof core.detectAccountConflict==="function"&&
    core.detectAccountConflict(document);
  async function rejectAccountConflict(){
    await stage("auth_context_changed");
    await report("unknown","account_context_changed");
  }
  async function getAssignedJob(){
    // Retry only handoff; never retry a click or a sent prompt.
    const deadline=Date.now()+12000;
    while(Date.now()<deadline){
      try{
        const task=await chrome.runtime.sendMessage({type:"chat-smoke-get",id:jobId});
        if(task?.ok && typeof task.prompt==="string" && task.prompt.length<=6000)return task;
      }catch(_){}
      await nap(200);
    }
    return null;
  }
  function seenUserMessage(promptHead){
    if(!savedChat())return false;
    const bubbles=document.querySelectorAll(
      '[data-message-author-role="user"],[data-testid="user-message"],[data-message-author-role="human"]');
    for(const node of bubbles){
      if(normalize(node.textContent).includes(promptHead))return true;
    }
    // UI versions may omit author-role attributes. Require both matching visible
    // text AND a cleared composer, in addition to the persisted /c/ route.
    const visibleText=normalize(document.body?.innerText||"");
    if(!visibleText.includes(promptHead))return false;
    const composer=core.findEditor(document);
    const composerValue=normalize(composer?.value??composer?.textContent??"");
    return !composerValue.includes(promptHead);
  }
  async function run(){
    const task=await getAssignedJob();
    if(!task){await stage("handoff_failed");return;}
    const minHoldMs=Number.isInteger(task.minHoldSeconds)?
      Math.max(5000,Math.min(180000,task.minHoldSeconds*1000)):5000;
    const completionMode=task.completionMode===true;
    await stage("assigned");
    if(accountConflict()){await rejectAccountConflict();return;}
    if(location.pathname!=="/"){await report("unknown","unexpected_route");return;}
    const editor=await waitFor(()=>core.findEditor(document),30000);
    if(!editor){await report("unknown","editor_not_found");return;}
    await stage("editor_found");
    if(!core.fillEditor(editor.ownerDocument||document,editor,task.prompt)){
      await report("unknown","editor_fill_failed");return;
    }
    await stage("prompt_filled");
    // Hydration can finish AFTER a background tab's contentEditable is populated.
    // A missing/disabled send button is pre-send, so re-sync the editor twice
    // without issuing any send, then fail closed if the button stays missing.
    let button=await waitFor(()=>core.findSendButton(document),12000);
    for(let i=0;!button && i<2;i++){
      const current=core.findEditor(document);
      if(!current){await report("unknown","editor_not_found");return;}
      if(!core.fillEditor(current.ownerDocument||document,current,task.prompt)){
        await report("unknown","editor_fill_failed");return;
      }
      await stage("prompt_filled");
      button=await waitFor(()=>core.findSendButton(document),12000);
    }
    if(!button){
      // A typed, privacy-safe UI signal distinguishes absent, disabled and
      // mis-scoped controls without collecting the user's prompt or account.
      const reason=typeof core.sendControlReason==="function"?
        core.sendControlReason(document):"button_absent";
      await stage("send_ui_diagnostic",reason);
      await report("unknown","send_button_missing");return;
    }
    if(accountConflict()){await rejectAccountConflict();return;}
    button.click(); // exactly once
    await stage("send_clicked");
    const head=normalize(task.prompt).slice(0,36);
    const deadline=Date.now()+Math.max(90000,Math.min(300000,
      (Number.isInteger(task.minHoldSeconds)?task.minHoldSeconds:5)*1000+90000));
    let committedAt=0,answer="",stableSince=0,wasCommitted=false;
    while(Date.now()<deadline){
      // A saved /c/ route can coexist with an account-change failure toast.
      // Never treat that DOM route alone as proof of server acceptance.
      if(accountConflict()){await rejectAccountConflict();return;}
      if(seenUserMessage(head)){
        if(!committedAt){
          committedAt=Date.now();wasCommitted=true;
          await stage("message_saved");
        }
      }
      if(wasCommitted){
        if(completionMode){
          // Retain the ChatGPT tab until the local bridge validates an
          // external MCP report and returns a durable scoped ACK.
          // A visible assistant answer is NOT a completion certificate.
          await nap(500);
          continue;
        }
        const assistants=document.querySelectorAll(
          '[data-message-author-role="assistant"],[data-testid="assistant-message"]');
        const text=assistants.length?
          normalize(assistants[assistants.length-1]?.textContent):"";
        const generating=!!document.querySelector(
          'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop"]');
        // If the response completed quickly, close promptly and mark done.
        if(text && !generating){
          if(text!==answer){answer=text;stableSince=Date.now();}
          if(Date.now()-stableSince>=(minHoldMs>5000?7000:1000)){
            await report("done","assistant_confirmed",{
            assistantSeen:true,userSeen:true,responseText:text
          });
            return;
          }
        }else{answer="";stableSince=0;}
        // Fast mode: close after a durable posted message even if GPT is still
        // generating; never claim reply completion in that case.
        if(Date.now()-committedAt>=minHoldMs){
          if(accountConflict()){await rejectAccountConflict();return;}
          await report("submitted",minHoldMs>5000?
            "submitted_after_min_hold":"submitted_after_5s",{userSeen:true});
          return;
        }
      }
      await nap(350);
    }
    if(completionMode && wasCommitted){
      // Signal no terminal result: the background service worker owns the
      // report ACK lifecycle, including the case where this script exits.
      await stage("message_saved");
      return;
    }
    await report("unknown",wasCommitted?"assistant_timeout":"user_message_not_confirmed");
  }
  run().catch(async()=>{await report("unknown","content_script_error");});
})();
