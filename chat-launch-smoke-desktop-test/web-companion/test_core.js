/* Mock-DOM regression tests; no real ChatGPT interaction. */
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const sandbox={};vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(__dirname+"/core.js","utf8"),sandbox);
const c=sandbox.ChatLaunchSmoke;
let checks=0;
function check(a,b){assert.equal(a,b);checks++;}
check(c.shouldRun({hostname:"chatgpt.com",pathname:"/",hash:"#chat-launch-smoke=v1"}),true);
check(c.shouldRun({hostname:"evil.example",pathname:"/",hash:"#chat-launch-smoke=v1"}),false);
check(c.shouldRun({hostname:"chatgpt.com",pathname:"/c/123",hash:"#chat-launch-smoke=v1"}),false);
check(c.shouldRun({hostname:"chatgpt.com",pathname:"/",hash:"#other"}),false);
function el(attrs={},tagName="DIV"){
  return {
    id:attrs.id||"",
    className:attrs.className||"",
    tagName,disabled:false,readOnly:false,
    isContentEditable:!!attrs.native,
    getAttribute(n){return Object.prototype.hasOwnProperty.call(attrs,n)?attrs[n]:null;},
    getBoundingClientRect(){return {width:300,height:45,top:450};},
    closest(){return null;},
    querySelectorAll(){return [];},
    focus(){this.focused=true;},
    dispatchEvent(evt){this.lastEvent=evt.type;},
    textContent:""
  };
}
const plain=el({contenteditable:"plaintext-only",className:"ProseMirror"});
check(c.isEditable(plain),true);
const typed=el({contenteditable:"true"});
check(c.isEditable(typed),true);
check(c.isEditable(el({contenteditable:"false"})),false);
const textarea=el({},"TEXTAREA");
check(c.isEditable(textarea),true);
textarea.readOnly=true;check(c.isEditable(textarea),false);textarea.readOnly=false;
function doc(list, extra={}){
  return {
    defaultView:{innerHeight:900,
      HTMLTextAreaElement:{prototype:{set value(v){this._value=v},get value(){return this._value}}},
      Event:class {constructor(type){this.type=type}},
      InputEvent:class {constructor(type){this.type=type}}},
    querySelectorAll(selector){if(selector==="*")return extra.all||[];
      return list.filter(x=>selector.split(",").some(s=>x.matchSelectors?.includes(s.trim())));},
    querySelector(){return null;},
    getSelection(){return {removeAllRanges(){},addRange(){}}},
    createRange(){return {selectNodeContents(){}}},
    execCommand(cmd,ui,value){this.didInsert=value;return true}
  };
}
plain.matchSelectors=[".ProseMirror","[contenteditable]"];
const d=doc([plain]);
check(c.findEditor(d),plain);
const shadowEditor=el({contenteditable:"plaintext-only",id:"prompt-textarea"});
shadowEditor.matchSelectors=["#prompt-textarea","[contenteditable]"];
const shadow=doc([shadowEditor]);const host=el({});
host.shadowRoot=shadow;
check(c.findEditor(doc([], {all:[host]})),shadowEditor);
check(c.findEditor(doc([])),null);
const weaker=el({contenteditable:"true"});weaker.matchSelectors=["[contenteditable]"];
const stronger=el({contenteditable:"plaintext-only",id:"prompt-textarea"});
stronger.matchSelectors=["#prompt-textarea","[contenteditable]"];
check(c.findEditor(doc([weaker,stronger])),stronger);
const textAreaDoc=doc([]);
Object.defineProperty(textarea,"value",{get(){return this._value},set(v){this._value=v}});
check(c.fillEditor(textAreaDoc,textarea,"Тест"),true);
check(textarea.value,"Тест");check(textarea.lastEvent,"input");
const rich=el({contenteditable:"true"});const richDoc=doc([]);
richDoc.execCommand=(cmd,ui,value)=>{rich.textContent=value;return true;};
check(c.fillEditor(richDoc,rich,"ПРИНЯТО"),true);
check(rich.textContent,"ПРИНЯТО");
const send=el({},"BUTTON");
send.matchSelectors=["button[data-testid='send-button']"];
function composerDoc(buttons=[]){
  const root=doc([plain,...buttons]);
  plain.ownerDocument=root;
  return root;
}
const sendDoc=composerDoc([send]);
check(c.findSendButton(sendDoc),send);
check(c.sendControlReason(sendDoc),"ready");
send.disabled=true;
check(c.findSendButton(sendDoc),null);
check(c.sendControlReason(sendDoc),"button_disabled");
send.disabled=false;
// The first candidate may be an inactive responsive duplicate.
const hiddenDuplicate=el({},"BUTTON");
hiddenDuplicate.matchSelectors=["button[data-testid='send-button']"];
hiddenDuplicate.disabled=true;
check(c.findSendButton(composerDoc([hiddenDuplicate,send])),send);
// A second equally valid button is ambiguous and must not be clicked.
const identical=el({},"BUTTON");
identical.matchSelectors=["button[data-testid='send-button']"];
check(c.findSendButton(composerDoc([send,identical])),null);
// Never take an unrelated Send button from another part of the page.
const distant=el({},"BUTTON");
distant.matchSelectors=["button[data-testid='send-button']"];
distant.getBoundingClientRect=()=>({width:40,height:40,top:30});
check(c.findSendButton(composerDoc([distant])),null);
check(c.findSendButton(doc([distant])),null);
// Shadow-root composer controls are eligible only in the editor's own root.
const shadowSend=el({},"BUTTON");
shadowSend.matchSelectors=["button[data-testid='composer-send-button']"];
const isolatedShadow=doc([shadowEditor,shadowSend]);
const outer=doc([],{all:[{tagName:"DIV",shadowRoot:isolatedShadow}]});
shadowEditor.getRootNode=()=>isolatedShadow;
shadowSend.getRootNode=()=>isolatedShadow;
check(c.findSendButton(outer),shadowSend);
// A scoped submit fallback is allowed, but a generic global submit is not.
const formSubmit=el({},"BUTTON");
const form={contains:x=>x===formSubmit,querySelectorAll:()=>[formSubmit]};
plain.closest=selector=>selector==="form"?form:null;
check(c.findSendButton(composerDoc()),formSubmit);
plain.closest=()=>null;
const alertNode=el({});alertNode.matchSelectors=['[role="alert"]'];
alertNode.textContent="Your account has changed. Please resend the message.";
check(c.detectAccountConflict(doc([alertNode])),true);
alertNode.textContent="Учётная запись сменилась. Повторите отправку.";
check(c.detectAccountConflict(doc([alertNode])),true);
alertNode.textContent="Sending your message";
check(c.detectAccountConflict(doc([alertNode])),false);
const promptNode=el({});promptNode.matchSelectors=['[data-message-author-role="user"]'];
promptNode.textContent="Describe the account changed error";
check(c.detectAccountConflict(doc([promptNode])),false);
const diagnostic=c.diagnostics(d);
check(diagnostic.roots,1);check(diagnostic.editables,1);
console.log("PASS: "+checks+" assertions; selector, shadow DOM, rich-text, readonly, send, reentry");
