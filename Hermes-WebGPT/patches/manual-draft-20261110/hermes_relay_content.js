/* Single temporary ChatGPT tab. Close ~5s after the posted user message is
 * visible in a persisted conversation. A "submitted" receipt does NOT claim
 * the assistant finished (closing early may interrupt generation).
 */
(()=>{
  "use strict";
  const hint=globalThis.__HermesRelayAssignedId;
  const match=location.hash.match(/^#hermes-relay=([0-9a-f-]{36})$/);
  if(window.top!==window)return;
  const jobId=hint||(match&&match[1]);
  if(!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(jobId||""))return;
  if(hint&&match&&hint!==match[1])return;
  if(globalThis.__HermesRelayContent)return;
  const core=globalThis.ChatLaunchSmoke;
  if(!core){
   void chrome.runtime.sendMessage({type:"hermes-relay-finished",id:jobId,status:"unknown",reason:"core_unavailable"}).catch(()=>{});return;
  }
  globalThis.__HermesRelayContentJobId=jobId;
  globalThis.__HermesRelayContent=true;
  if(match)history.replaceState(history.state,"",location.pathname+location.search);
  const nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
   const normalize=text=>String(text||"").replace(/\s+/g," ").trim();
   const USER_SELECTOR='[data-message-author-role="user"],[data-testid="user-message"],'+
     '[data-message-author-role="human"],[data-content-search-unit-key$=":user"]';
   function userBubbles(){
     return [...document.querySelectorAll(USER_SELECTOR)].filter(n=>n?.isConnected!==false);
   }

    function normalizeReplyText(raw){
      let value=String(raw||"").trim();
      // These labels belong to ChatGPT's authored-turn wrapper, not the reply.
      value=value.replace(/^(?:ChatGPT сказал:|ChatGPT said:)\s*/u,"").trim();
      const fence=value.match(/^\x60\x60\x60(?:json)?\s*\r?\n([\s\S]*?)\r?\n\x60\x60\x60$/i);
      if(fence)value=fence[1].trim();
      try{
        const data=JSON.parse(value);
        if(data&&data.request_id===jobId&&
           (typeof data.content==="string"||data.content===null)&&Array.isArray(data.tool_calls))
          return JSON.stringify(data);
      }catch(_){}
      return value; // Relay still rejects malformed, unbound or unoffered results.
    }
   function responseText(assistant){
     if(!assistant)return "";
     // Codeblock copy controls are outside the generated JSON. Read the
     // actual code text, never button/toolbar labels appended by innerText.
     const code=[...assistant.querySelectorAll?.("pre code")||[]];
     if(code.length===1){
       const raw=String(code[0].textContent||"").trim();
       if(raw)return normalizeReplyText(raw);
     }
     return normalizeReplyText(assistant.innerText||assistant.textContent||"");
   }
   function boundAnswer(text,id){
     let value=String(text||"").trim();
     if(value.startsWith("\`\`\`json\n")&&value.endsWith("\n\`\`\`"))
       value=value.slice(8,-4).trim();
     else if(value.startsWith("\`\`\`\n")&&value.endsWith("\n\`\`\`"))
       value=value.slice(4,-4).trim();
     try{
       const data=JSON.parse(value);
       return !!data&&data.request_id===id&&
         (typeof data.content==="string"||data.content===null)&&
         Array.isArray(data.tool_calls);
     }catch(_){return false;}
   }
   // ChatGPT may render a large sent paste as an attachment/tile rather
   // than echo its text. Only an otherwise EMPTY owned new chat with one
   // new user bubble AND a completed, ID-bound assistant JSON can confirm it.
   // Never trust an arbitrary bubble, a route alone, or an unbound answer.
   function boundLargePasteUser(prompt,id){
     if(!savedChat()||String(prompt).length<12000)return null;
     const bubbles=userBubbles();
     if(bubbles.length!==1)return null;
     const user=bubbles[0];
     const assistant=assistantAfter(user);
     if(!assistant||!boundAnswer(responseText(assistant),id))return null;
     const edit=core.findEditor(document);
     if(edit&&String(core.readEditorRaw(edit)||"").trim())return null;
     return user;
   }
  const savedChat=()=>/^\/c\/[0-9a-z-]+(?:\/|$)/.test(location.pathname);
  async function stage(value,reason=""){
    try{return await chrome.runtime.sendMessage({
      type:"hermes-relay-stage",id:jobId,stage:value,reason,
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
        type:"hermes-relay-finished",id:jobId,status,reason,assistantSeen,userSeen,
        responseText:status==="done"?String(responseText||"").slice(0,200000):"",
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
        const task=await chrome.runtime.sendMessage({type:"hermes-relay-get",id:jobId});
        if(task?.ok && typeof task.prompt==="string" && task.prompt.length<=120000)return task;
      }catch(_){}
      await nap(200);
    }
    return null;
  }
  function savedUserMessage(exactPrompt){
    // The page body, sidebar or an assistant quote is NOT a saved user turn.
    // Require a real authored user bubble with the complete normalized draft.
    if(!savedChat()||!exactPrompt)return null;
     const bubbles=userBubbles();
    const wanted=normalize(exactPrompt);
    // The last authored user turn is the only candidate. If another user
    // submitted a later message, an assistant reply belongs to that turn,
    // not to our earlier owned attempt.
    const node=bubbles[bubbles.length-1];
    if(!node||node.isConnected===false)return null;
    const body=normalize(node.innerText||node.textContent);
    // A quoted or wrapped copy of our draft is not the sent turn.
    return body===wanted?node:null;
  }
  function assistantAfter(userNode){
    // Do not attribute an earlier response in a reused/stale conversation
    // to this attempt. Browser DOM document order is the evidence boundary.
    if(!userNode||userNode.isConnected===false||
       typeof userNode.compareDocumentPosition!=="function")return null;
    const assistants=document.querySelectorAll(
      '[data-message-author-role="assistant"],[data-testid="assistant-message"],[data-content-search-unit-key$=":assistant"]');
    for(let i=assistants.length-1;i>=0;i--){
      const node=assistants[i];
      if(node===userNode||node.isConnected===false)continue;
      const position=userNode.compareDocumentPosition(node);
      // DOCUMENT_POSITION_DISCONNECTED can include arbitrary PRECEDING/FOLLOWING bits.
      if(!(position&1)&&(position&4))return node;
    }
    return null;
  }
   let userIntervened=false;
   const activityEvents=["keydown","paste","drop","pointerdown"];
   const recordActivity=e=>{if(e.isTrusted)userIntervened=true;};
   for(const type of activityEvents)document.addEventListener?.(type,recordActivity,true);
   async function run(){
    const task=await getAssignedJob();
    if(!task){await report("unknown","handoff_failed");return;}
    const minHoldMs=Number.isInteger(task.minHoldSeconds)?
      Math.max(5000,Math.min(180000,task.minHoldSeconds*1000)):5000;
    const completionMode=task.completionMode===true;
    await stage("assigned");
    if(accountConflict()){await rejectAccountConflict();return;}
    if(location.pathname!=="/"){await report("unknown","unexpected_route");return;}
    // The owner prefers High, but mode selection is advisory for Hermes.
    // Never discard a user's message solely because the effort control is
    // absent, different, ambiguous or not verifiable.
    let lastEffortWarning="";
    const warnEffort=async reason=>{
      const code=typeof reason==="string"&&/^[a-z_]{1,60}$/.test(reason)?
        reason:"high_unverified";
      if(code===lastEffortWarning)return;
      lastEffortWarning=code;
      await stage("reasoning_warning",code); // best effort, not Send authorization
    };
    let high={ok:false,reason:"high_probe_unavailable"};
    try{
      if(typeof core.ensureHigh==="function")
        high=await core.ensureHigh(document,{waitMs:1200});
    }catch(_){}
    if(!high?.ok||high.level!=="High"||high.verified!==true)
      await warnEffort(high?.reason);
    const editor=await waitFor(()=>core.findEditor(document),30000);
    if(!editor){
      const reason=typeof core.editorFailureReason==="function"?
        core.editorFailureReason(document):"editor_not_found";
      await report("unknown",reason);return;
    }
    await stage("editor_found");
    const initialDraft=String(core.readEditorRaw(editor)||"");
    if(initialDraft.trim()){
      let permission=null,draftSha="";
      try{
       const bytes=new TextEncoder().encode(initialDraft);
       const sha=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),b=>b.toString(16).padStart(2,"0")).join("");
       draftSha=sha;
       permission=await chrome.runtime.sendMessage({type:"hermes-relay-recover-owned-draft",id:jobId,sha256:sha,length:initialDraft.length});
      }catch(_){}
      // A user/unknown draft stays untouched. Only a durable, exact match to
      // this extension's failed PRE-SEND attempt can be replaced by a NEW job.
      if(permission?.ok!==true||permission.protocol!==1||permission.quarantined!==true||permission.sha256!==draftSha||
         permission.length!==initialDraft.length||userIntervened||document.visibilityState==="visible"||
         location.pathname!=="/"||accountConflict()||core.findEditor(document)!==editor||
         String(core.readEditorRaw(editor)||"")!==initialDraft){
       await report("unknown","manual_input");return;
      }
    }
    if(!core.fillEditor(editor.ownerDocument||document,editor,task.prompt)){
      await report("unknown","editor_fill_failed");return;
    }
    await stage("prompt_filled");
    // Hydration can finish AFTER a background tab's contentEditable is populated.
    // A missing/disabled send button is pre-send, so re-sync the editor twice
    // without issuing any send, then fail closed if the button stays missing.
    let button=await waitFor(()=>core.findSendButton(document),12000);
    if(!button){
      // React can hydrate a contentEditable after input sync. Only replay
      // *input synchronization*, never Send. Do not touch foreign user edits,
      // switched accounts, changed routes, or disconnected editors.
      for(let attempt=0;attempt<2&&!button;attempt++){
        if(accountConflict()||location.pathname!=="/")break;
        const current=core.findEditor(document);
        if(!current||current.isConnected===false||
           core.readEditorRaw(current)!==task.prompt)break;
        if(!core.fillEditor(current.ownerDocument||document,current,task.prompt))break;
        button=await waitFor(()=>core.findSendButton(document),3500);
      }
    }
    if(!button){
      const reason=typeof core.sendControlReason==="function"?
        core.sendControlReason(document):"button_absent";
      await stage("send_ui_diagnostic",reason);
      // Preserve the typed failure reason without exposing prompt or DOM text.
      const known=["button_absent","button_disabled","button_not_scoped","editor_absent"];
      await report("unknown",known.includes(reason)?reason:"send_button_missing");return;
    }
    if(accountConflict()){await rejectAccountConflict();return;}
    // The wait/re-sync can yield to navigation, user edits or React hydration.
    // Re-check the exact owned composer, route and button immediately before Send.
    // Intent is a Relay state transition, not the browser Send click.
    // Its HTTP acknowledgement may be lost although Relay committed intent.
    // Reconcile with READ-ONLY preflight before ONE button.click(). Never
    // repeat the intent POST or resend an unconfirmed ChatGPT message.
    try{
      await chrome.runtime.sendMessage({type:"hermes-relay-intent",id:jobId});
    }catch(_){}
    let intentVerified=false;
    for(let proof=0;proof<3;proof++){
      try{
        const live=await chrome.runtime.sendMessage({type:"hermes-relay-preflight",id:jobId});
        if(live?.ok===true){intentVerified=true;break;}
      }catch(_){}
      if(proof<2)await nap(200);
    }
    if(!intentVerified){await report("unknown","uncertain_send");return;}
    const finalButton=core.findSendButton(document);
    const finalEditor=core.findEditor(document);
    const finalRaw=finalEditor&&(typeof core.readEditorRaw==="function"?
      core.readEditorRaw(finalEditor):String(finalEditor.value??finalEditor.textContent??""));
    if(location.pathname!=="/"||
       (location.origin&&location.origin!=="https://chatgpt.com")||
       !finalEditor||finalRaw!==task.prompt||finalButton!==button){
      await report("unknown","send_preflight_changed");return;
    }
    // Observe effort once more, but keep the exact-once Send ownership gates.
    let finalEffort=null;
    try{
      if(typeof core.selectedReasoning==="function")
        finalEffort=core.selectedReasoning(document);
    }catch(_){}
    if(!finalEffort?.ok||finalEffort.level!=="High")
      await warnEffort(finalEffort?.reason||"high_not_selected");
    if(location.pathname!=="/"||core.findEditor(document)!==finalEditor||
       core.readEditorRaw(finalEditor)!==task.prompt||core.findSendButton(document)!==button||accountConflict()){
      await report("unknown","send_preflight_changed");return;
    }
    button.click(); // exactly once
    await stage("send_clicked");
     // Do not trap Telegram in a 20-minute wait when a sent paste cannot
     // be confirmed. The in-flight intent stays non-repeatable either way.
     const sentAt=Date.now();
     const unconfirmedDeadline=sentAt+75000;
     const deadline=sentAt+180000;
    let committedAt=0,answer="",stableSince=0,wasCommitted=false;
    while(Date.now()<deadline){
      // A saved /c/ route can coexist with an account-change failure toast.
      // Never treat that DOM route alone as proof of server acceptance.
      if(accountConflict()){await rejectAccountConflict();return;}
       const userNode=savedUserMessage(task.prompt)||
         boundLargePasteUser(task.prompt,jobId);
      if(userNode){
        if(!committedAt){
          committedAt=Date.now();wasCommitted=true;
          const saved=await stage("message_saved");
          if(!saved?.ok){await report("unknown","uncertain_send");return;}
        }
      }
      if(wasCommitted){
        if(!userNode){
          // Never claim reply completion from a stale route or missing turn.
          answer="";stableSince=0;
          await nap(350);
          continue;
        }
        if(completionMode){
          // Retain the ChatGPT tab until the local bridge validates an
          // external MCP report and returns a durable scoped ACK.
          // A visible assistant answer is NOT a completion certificate.
          await nap(500);
          continue;
        }
        const assistant=assistantAfter(userNode);
         const text=responseText(assistant);
        const generating=!!document.querySelector(
          'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop"]');
        // If the response completed quickly, close promptly and mark done.
        if(text && !generating){
          if(text!==answer){answer=text;stableSince=Date.now();}
          if(Date.now()-stableSince>=5000){
            await report("done","assistant_confirmed",{
            assistantSeen:true,userSeen:true,responseText:text
          });
            return;
          }
        }else{answer="";stableSince=0;}
        // Fast mode: close after a durable posted message even if GPT is still
        // generating; never claim reply completion in that case.

      }
       if(!wasCommitted&&Date.now()>=unconfirmedDeadline){
         await report("unknown","user_message_not_confirmed");return;
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
  run().catch(async()=>{await report("unknown","content_script_error");}).finally(()=>{
   for(const type of activityEvents)document.removeEventListener?.(type,recordActivity,true);
  });
})();


