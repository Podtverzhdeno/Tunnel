/* One-shot ChatGPT browser smoke test: no private API or background traffic. */
((root)=>{
"use strict";
const HASH="#chat-launch-smoke=v1";
const PROMPT="Тест нового чата через локальный веб-компаньон. Ответь одним словом: ПРИНЯТО.";
const EDITORS=["#prompt-textarea","[data-testid='composer-input']",".ProseMirror",
  "[data-lexical-editor]","[contenteditable]","textarea","[role='textbox']"];
const SEND_BUTTONS=["button[data-testid='send-button']","button[data-testid='composer-send-button']",
  "button[aria-label='Send prompt']","button[aria-label='Send message']",
  "button[aria-label='Отправить сообщение']","button[aria-label='Отправить']",
  "button[data-testid='composer-submit-button']",
  "button[aria-label='Send']","button[aria-label='Send now']"];
function shouldRun(url){return url.hostname==="chatgpt.com" && url.pathname==="/" && url.hash===HASH;}
function isEditable(el){
  if(!el||typeof el.getAttribute!=="function")return false;
  if(el.tagName==="TEXTAREA")return !el.disabled&&!el.readOnly;
  const a=el.getAttribute("contenteditable");
  return !!el.isContentEditable || (a!==null&&a!=="false"&&a!=="inherit");
}
function isVisible(el){
  if(el.getAttribute?.("aria-hidden")==="true")return false;
  const r=el.getBoundingClientRect?.();
  return !r||(r.width>1&&r.height>1);
}
function roots(doc){
  const rs=[doc],seen=new Set(rs);
  for(let i=0;i<rs.length&&i<12;i++){
    const r=rs[i];
    for(const el of r.querySelectorAll?.("*")||[]){
      if(el.shadowRoot&&!seen.has(el.shadowRoot)){rs.push(el.shadowRoot);seen.add(el.shadowRoot);}
      if(el.tagName==="IFRAME")try{
        if(el.contentDocument&&!seen.has(el.contentDocument)){
          rs.push(el.contentDocument);seen.add(el.contentDocument);
        }
      }catch(_){}
      if(rs.length>=12)break;
    }
  }
  return rs;
}
function findEditor(doc){
  const seen=new Set();
  for(const r of roots(doc))for(const sel of EDITORS){
    for(const el of r.querySelectorAll?.(sel)||[]){
      if(isEditable(el)&&isVisible(el))seen.add(el);
      else for(const inner of el.querySelectorAll?.("[contenteditable],textarea")||[]){
        if(isEditable(inner)&&isVisible(inner))seen.add(inner);
      }
    }
  }
  function score(el){
    let s=0;
    if(el.id==="prompt-textarea")s+=150;
    if(el.getAttribute("data-testid")==="composer-input")s+=130;
    if(el.getAttribute("data-lexical-editor")!==null)s+=110;
    if(String(el.className).includes("ProseMirror"))s+=100;
    if(el.getAttribute("role")==="textbox")s+=70;
    if(el.tagName==="TEXTAREA")s+=60;
    if(el.closest?.("form"))s+=15;
    const r=el.getBoundingClientRect?.();
    if(r&&r.top>(doc.defaultView?.innerHeight||600)/3)s+=10;
    return s;
  }
  return [...seen].sort((a,b)=>score(b)-score(a))[0]||null;
}
function findSendButton(doc){
  // Never click the first matching button in the entire document. Responsive
  // layouts can contain disabled/hidden duplicates, unrelated dialogs, and
  // editor/send controls inside open Shadow DOM.
  const editor=findEditor(doc);
  if(!editor)return null;
  const form=editor.closest?.("form")||null;
  const composer=editor.closest?.(
    '[data-testid="composer"],[data-testid="composer-root"],[data-testid="composer-container"]')||null;
  const editorRoot=editor.getRootNode?.()||editor.ownerDocument||doc;
  const er=editor.getBoundingClientRect?.();
  const candidates=new Map();
  const allowed=el=>!!el&&!el.disabled&&el.getAttribute?.("aria-disabled")!=="true"&&
    el.getAttribute?.("aria-busy")!=="true"&&
    !el.closest?.('[aria-hidden="true"],[hidden],[inert]')&&isVisible(el);
  function consider(el,root,explicit){
    if(!allowed(el))return;
    const insideForm=!!form&&form.contains?.(el)===true;
    const insideComposer=!!composer&&composer.contains?.(el)===true;
    const sameRoot=(el.getRootNode?.()||root)===editorRoot;
    if(!insideForm&&!insideComposer&&!sameRoot)return;
    const br=el.getBoundingClientRect?.();
    // A global explicit send button is acceptable only if geometrically
    // adjacent to the active composer. Do not hit another form's Send control.
    if(!insideForm&&!insideComposer){
      if(!explicit||!er||!br)return;
      const distance=Math.abs((er.top+er.height/2)-(br.top+br.height/2));
      if(distance>220)return;
      if(el.closest?.("form")&&el.closest("form")!==form)return;
    }
    const score=(insideForm?100:0)+(insideComposer?80:0)+(explicit?40:0)+
      (br&&er?Math.max(0,30-Math.round(Math.abs(br.top-er.top)/10)):0);
    candidates.set(el,Math.max(score,candidates.get(el)||0));
  }
  const selectors=SEND_BUTTONS.join(",");
  for(const root of roots(doc)){
    const all=root.querySelectorAll?.(selectors);
    if(all){for(const el of all)consider(el,root,true);}
    else for(const selector of SEND_BUTTONS){
      consider(root.querySelector?.(selector),root,true);
    }
  }
  // Submit fallback is permitted only inside the exact form of the composer.
  if(form){
    const all=form.querySelectorAll?.('button[type="submit"]');
    if(all){for(const el of all)consider(el,editorRoot,false);}
    else consider(form.querySelector?.('button[type="submit"]'),editorRoot,false);
  }
  const ranked=[...candidates].sort((a,b)=>b[1]-a[1]);
  if(!ranked.length)return null;
  // Two equal candidates are ambiguous; guessing could submit another form.
  if(ranked.length>1&&ranked[0][1]===ranked[1][1])return null;
  return ranked[0][0];
}
function fillEditor(doc,el,text){
  if(!isEditable(el))return false;
  el.focus();
  if(el.tagName==="TEXTAREA"){
    const setter=Object.getOwnPropertyDescriptor(doc.defaultView.HTMLTextAreaElement.prototype,"value")?.set;
    if(setter)setter.call(el,text); else el.value=text;
    el.dispatchEvent(new doc.defaultView.Event("input",{bubbles:true}));
    return el.value===text;
  }
  try{
    const sel=doc.getSelection?.(),range=doc.createRange?.();
    if(sel&&range){range.selectNodeContents(el);sel.removeAllRanges();sel.addRange(range);}
  }catch(_){}
  let done=false;
  try{done=!!doc.execCommand?.("insertText",false,text);}catch(_){}
  if(!done||!el.textContent?.includes(text)){
    el.textContent=text;
  }
  // Rich React/ProseMirror editors may hydrate after execCommand. Always
  // emit input synchronization, even when the native insertion returned true.
  const EventType=doc.defaultView.InputEvent||doc.defaultView.Event;
  el.dispatchEvent(new EventType("input",{bubbles:true,data:text,inputType:"insertText"}));
  return !!el.textContent?.includes(text);
}
// Detect only explicit account/session-switch errors in ChatGPT UI notices.
 // Never read cookies, email, profile identifiers or browser accounts.
function detectAccountConflict(doc){
  const targets='[role="alert"],[role="status"],[role="dialog"],[data-sonner-toast],'+
    '[data-testid*="toast"],[data-testid*="error"]';
  const expressions=[
    /\baccount (?:has |was )?(?:changed|switched)\b/i,
    /\b(?:you(?: have|'ve)? (?:switched|changed) accounts?)\b/i,
    /\b(?:signed|logged) in (?:as|with) (?:a )?different account\b/i,
    /\b(?:session|login) (?:has )?(?:changed|switched)\b/i,
    /уч[её]тн(?:ая|ую) запис[ьи] (?:была )?(?:сменилась|изменилась|переключена)/i,
    /(?:вы )?(?:сменили|переключили) (?:аккаунт|уч[её]тную запись)/i
  ];
  for(const el of doc.querySelectorAll?.(targets)||[]){
    if(!isVisible(el))continue;
    const msg=String(el.innerText||el.textContent||"").replace(/\s+/g," ").slice(0,600);
    if(expressions.some(re=>re.test(msg)))return true;
  }
  return false;
}
// Bounded, non-sensitive diagnostics: never inspect prompt/account content.
function sendControlReason(doc){
  if(!findEditor(doc))return "editor_absent";
  if(findSendButton(doc))return "ready";
  const found=new Set();
  for(const root of roots(doc)){
    const all=root.querySelectorAll?.(SEND_BUTTONS.join(","));
    if(all){for(const el of all)found.add(el);}
    else for(const selector of SEND_BUTTONS){
      const el=root.querySelector?.(selector);if(el)found.add(el);
    }
  }
  if(!found.size)return "button_absent";
  if(![...found].some(el=>isVisible(el)&&!el.disabled&&
    el.getAttribute?.("aria-disabled")!=="true"&&
    el.getAttribute?.("aria-busy")!=="true")){
    return "button_disabled";
  }
  return "button_not_scoped";
}
function diagnostics(doc){
  let editables=0,textboxes=0;
  const rs=roots(doc);
  for(const r of rs){
    editables+=r.querySelectorAll?.("[contenteditable],textarea").length||0;
    textboxes+=r.querySelectorAll?.("[role='textbox']").length||0;
  }
  return {roots:rs.length,editables,textboxes};
}
root.ChatLaunchSmoke=Object.freeze({HASH,PROMPT,shouldRun,isEditable,findEditor,
  findSendButton,fillEditor,detectAccountConflict,sendControlReason,diagnostics});
})(globalThis);
