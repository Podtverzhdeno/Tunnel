/**
 * Hermes AutoPilot 0.8.5: bounded ChatGPT Web readiness.
 * Existing supervisor owns tabs; this module never creates/sends/closes tabs.
 * inspectChatComposer is self-contained for chrome.scripting.executeScript.
 */
export function inspectChatComposer(options = {}) {
  const out = (state, detail={}) => ({
    schema: 1, state, origin: location.origin,
    pathname: location.pathname, ...detail
  });
  if (location.origin !== 'https://chatgpt.com' ||
      !(/^\/$/.test(location.pathname) ||
        /^\/c\/[A-Za-z0-9_-]+\/?$/.test(location.pathname))) {
    return out('FOREIGN_DOCUMENT');
  }
  if (location.hash) return out('FOREIGN_DOCUMENT');
  if (navigator.onLine === false) return out('OFFLINE');
  if (document.readyState === 'loading' || !document.body) {
    return out('LOADING');
  }

  const visible = node => {
    if (!node?.isConnected || node.closest?.('[inert],[aria-hidden="true"]')) return false;
    const r=node.getBoundingClientRect?.();
    if (!r || r.width<=0 || r.height<=0) return false;
    const style=getComputedStyle(node);
    return style.visibility!=='hidden' && style.display!=='none';
  };
  const label = node => (node?.innerText || node?.textContent ||
    node?.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim().toLowerCase();
  // Mirror Web Companion 0.9.7's conservative mode evidence:
  // explicit selected attributes OR a unique visible composer placeholder.
  const choiceState = node => {
    for (const [key,on,off] of [
      ['aria-selected','true','false'],['aria-pressed','true','false'],
      ['aria-checked','true','false'],['data-state','active','inactive'],
      ['data-selected','true','false']
    ]) {
      const state=node?.getAttribute(key)?.toLowerCase();
      if(state===on)return true;
      if(state===off)return false;
    }
    return null;
  };
  const CHAT = new Set(['чат','chat']);
  const WORK = new Set(['работа','work']);
  const groupOf = node => node.closest('[role="tablist"],[role="radiogroup"]') ||
    node.parentElement;
  const controls=[...document.querySelectorAll(
    'button,[role="tab"],[role="radio"],[role="button"]')].filter(visible);
  const groups=[];
  const cap=Math.min(240,window.innerHeight*.28);
  for(const chat of controls.filter(x=>CHAT.has(label(x)))) {
    for(const work of controls.filter(x=>WORK.has(label(x)))) {
      if(chat===work || !groupOf(chat) || groupOf(chat)!==groupOf(work))continue;
      const c=chat.getBoundingClientRect(),w=work.getBoundingClientRect();
      if(c.top<0||w.top<0||c.top>cap||w.top>cap)continue;
      groups.push({chat,work});
    }
  }
  if(groups.length>1)return out('AMBIGUOUS_MODE');
  let chatModeConfirmed=false;
  if(groups.length===1){
    const {chat,work}=groups[0];
    const chatSelected=choiceState(chat),workSelected=choiceState(work);
    if(chatSelected===true&&workSelected===true)return out('AMBIGUOUS_MODE');
    let placeholder='unknown';
    const inputNodes=[...document.querySelectorAll(
      '[data-placeholder],[placeholder],[aria-placeholder]')].filter(visible);
    for(const el of inputNodes){
      for(const key of ['data-placeholder','placeholder','aria-placeholder']){
        const value=(el.getAttribute(key)||'').trim().toLowerCase();
        if(/^(работайте с chatgpt|work with chatgpt)$/.test(value))placeholder='work';
        if(/^(спросить chatgpt|спросите chatgpt|спросите что угодно|ask chatgpt|ask anything|message chatgpt)$/.test(value) &&
           placeholder!=='work')placeholder='chat';
      }
    }
    if(workSelected===true||placeholder==='work'){
      if(options.switchToChat===true){
        // A click is a request, never readiness; the next probe must confirm.
        try{chat.click();}catch{return out('MODE_CLICK_FAILED');}
        return out('MODE_SWITCH_REQUESTED');
      }
      return out('WORK_MODE');
    }
    chatModeConfirmed=(chatSelected===true && workSelected!==true) ||
      (placeholder==='chat' && workSelected!==true);
    if(!chatModeConfirmed)return out('MODE_SELECTION_UNVERIFIED');
  }else {
    // On saved /c/ conversations the mode toggle can disappear. An
    // editable composer is NOT proof that the same Chat mode persisted.
    // A trusted Web Companion lease may establish that separately.
    const login=controls.some(x=>/^(войти|log in|sign in)$/.test(label(x)));
    return out(login?'AUTH_REQUIRED':'MODE_CONTROL_NOT_VERIFIED');
  }

  const byId=[...document.querySelectorAll('#prompt-textarea')].filter(visible);
  if(byId.length>1)return out('AMBIGUOUS_COMPOSER');
  let editors=byId;
  if(editors.length===0){
    const fallback=[
      '[data-testid="composer-input"][contenteditable="true"]',
      'textarea[data-testid="prompt-textarea"]',
      'div.ProseMirror[contenteditable="true"]'
    ];
    editors=[...new Set(fallback.flatMap(s=>
      [...document.querySelectorAll(s)].filter(visible)))];
  }
  if(editors.length>1)return out('AMBIGUOUS_COMPOSER');
  if(!editors.length){
    const login=[...document.querySelectorAll('a[href*="auth/login"],a[href*="/login"],button')]
      .filter(visible).some(x=>/^(войти|log in|sign in)$/.test(label(x)));
    return out(login?'AUTH_REQUIRED':'COMPOSER_NOT_READY');
  }
  const editor=editors[0];
  const textarea=editor.tagName==='TEXTAREA';
  if(!textarea && !(editor.isContentEditable ||
       editor.getAttribute('contenteditable')==='true'))return out('COMPOSER_DISABLED');
  if(editor.hasAttribute('disabled') ||
     editor.getAttribute('aria-disabled')==='true' ||
     editor.getAttribute('contenteditable')==='false')return out('COMPOSER_DISABLED');
  const root=editor.closest('form,[data-testid="composer"],[data-testid="composer-root"]');
  const attachments=root?.querySelectorAll(
    '[data-attachment-id],[data-testid="attachment-chip"],[data-testid="file-chip"]'
  ).length || 0;
  const content=(textarea?editor.value:editor.innerText) || '';
  return out('READY',{
    editorKind:textarea?'textarea':'contenteditable',
    editorEmpty:content.trim().length===0,
    hasAttachments:attachments>0,
    chatModeConfirmed,
    // Only metadata crosses the extension boundary. Never include user text.
    hasModeControl:groups.length===1
  });
}
export async function ensureChatReady({
  chromeAPI, tabId, assertOwned, timeoutMs=15000,
  maxAttempts=8, waitMs=250, allowModeSwitch=true, sleep=ms=>new Promise(r=>setTimeout(r,ms))
}={}) {
  if(!chromeAPI?.tabs?.get || !chromeAPI?.scripting?.executeScript ||
     !Number.isInteger(tabId) || tabId<=0 ||
     typeof assertOwned!=='function' ||
     !Number.isInteger(timeoutMs)||timeoutMs<100||timeoutMs>120000 ||
     !Number.isInteger(maxAttempts)||maxAttempts<1||maxAttempts>30) {
    throw Error('CHAT_READINESS_CONFIG_INVALID');
  }
  const started=Date.now();
  let switched=!allowModeSwitch,switchAttempted=false,last='NOT_PROBED',attempt=0;
  for(;attempt<maxAttempts;attempt++){
    if(await assertOwned()!==true)return {schema:1,state:'OWNERSHIP_LOST',attempts:attempt};
    let tab;
    try{tab=await chromeAPI.tabs.get(tabId);}
    catch{return {schema:1,state:'TAB_UNAVAILABLE',attempts:attempt};}
    let url;
    try{url=new URL(tab.url||tab.pendingUrl);}catch{return {schema:1,state:'TAB_NAVIGATING',attempts:attempt};}
    if(url.origin!=='https://chatgpt.com' ||
       !(/^\/$/.test(url.pathname) ||
         /^\/c\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) || url.hash){
      return {schema:1,state:'FOREIGN_TAB',attempts:attempt};
    }
    const expected=url.origin+url.pathname+url.search;
    const pending=tab.pendingUrl?new URL(tab.pendingUrl):null;
    if(pending && (pending.origin+pending.pathname+pending.search!==expected ||
                   pending.hash))return {schema:1,state:'TAB_NAVIGATING',attempts:attempt};
    if(tab.status!=='complete'){
      last='TAB_LOADING';
    }else {
      let probe;
      try{
        const r=await chromeAPI.scripting.executeScript({
          target:{tabId,frameIds:[0]},
          func:inspectChatComposer,args:[{switchToChat:switched===false}]
        });
        if(!Array.isArray(r)||r.length!==1||r[0].frameId!==0 ||
           typeof r[0].documentId!=='string' || r[0].documentId.length<12 ||
           r[0].result?.schema!==1 || r[0].result?.origin!=='https://chatgpt.com' ||
           r[0].result?.pathname!==url.pathname){
          return {schema:1,state:'DOCUMENT_NOT_PROVEN',attempts:attempt+1};
        }
        probe={...r[0].result,documentId:r[0].documentId};
      }catch{
        last='DOM_PROBE_FAILED';
      }
      if(probe){
        last=probe.state;
        if(last==='MODE_SWITCH_REQUESTED'){switched=true;switchAttempted=true;}
        if(last==='READY'){
          // Re-check ownership and tab after asynchronous DOM operations.
          if(await assertOwned()!==true)return {schema:1,state:'OWNERSHIP_LOST',attempts:attempt+1};
          const again=await chromeAPI.tabs.get(tabId).catch(()=>null);
          let afterURL=null;
          try{afterURL=new URL(again?.url||again?.pendingUrl);}catch{}
          if(!again || again.id!==tabId || again.status!=='complete' ||
             !afterURL || afterURL.hash ||
             afterURL.origin+afterURL.pathname+afterURL.search!==expected ||
             (again.pendingUrl && again.pendingUrl!==again.url)){
            return {schema:1,state:'TAB_CHANGED',attempts:attempt+1};
          }
          return {schema:1,state:'READY',attempts:attempt+1,
            tabId,documentId:probe.documentId,
            editorEmpty:probe.editorEmpty,
            hasAttachments:probe.hasAttachments,
            chatModeConfirmed:probe.chatModeConfirmed,
            hasModeControl:probe.hasModeControl,
            modeSwitchRequested:switchAttempted};
        }
        if(['FOREIGN_DOCUMENT','OFFLINE','AUTH_REQUIRED',
            'AMBIGUOUS_MODE','AMBIGUOUS_COMPOSER',
            'COMPOSER_DISABLED','MODE_CLICK_FAILED',
            'MODE_CONTROL_NOT_VERIFIED','MODE_SELECTION_UNVERIFIED'].includes(last)){
          return {schema:1,state:last,attempts:attempt+1};
        }
      }
    }
    if(Date.now()-started>=timeoutMs)break;
    await sleep(Math.min(waitMs*(attempt+1),2000));
  }
  return {schema:1,state:'CHAT_READY_TIMEOUT',lastObservation:last,
    modeSwitchRequested:switchAttempted,
    attempts:attempt};
}
