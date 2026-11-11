"use strict";
// Real content-script control flow with synthetic DOM/runtime and virtual time.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2]||path.join(__dirname,'hermes_relay_content.js'),'utf8');
const ID='00000000-0000-4000-8000-000000000001';
const OTHER='00000000-0000-4000-8000-000000000002';

async function scenario(o={}){
 let now=10000,clickedAt=0,clicks=0,intents=0,final=null,stages=[],userNodes=[],assistantNodes=[];
 const listeners={};
 const prompt=o.short?'A short request with its unique protocol ID '+ID:'Owned model request '+ID+' '+ 'x'.repeat(13000);
 const location={origin:'https://chatgpt.com',pathname:'/',search:'',hash:'#hermes-relay='+ID,
   get href(){return this.origin+this.pathname;}};
 const response=JSON.stringify({request_id:o.wrongId?OTHER:ID,content:'READY',tool_calls:o.invalidTools?{}:[]});
 const user=(body)=>({innerText:body,textContent:body,isConnected:!o.disconnected,
   parentElement:{closest:()=>null},
   compareDocumentPosition:()=>o.disconnected?5:o.assistantBefore?2:4});
 const assistant=(text)=>({innerText:text,textContent:text,isConnected:true,
   querySelectorAll:()=>o.codeBlock?[{textContent:response}]:[]});
 if(o.preexistingTranscript){userNodes=[user('An older user turn')];assistantNodes=[assistant('An older answer')];}
 const doc={
   querySelectorAll(selector){
     if(/assistant/.test(selector))return o.answerDelay&&now-clickedAt<o.answerDelay?[]:assistantNodes;
     return userNodes;
   },
   querySelector(){return o.generating&&clicks?{}:null;},
   addEventListener(type,fn){listeners[type]=fn;},
   removeEventListener(type){delete listeners[type];},visibilityState:'hidden'
 };
 const editor={value:o.initialDraft||'',ownerDocument:doc,isConnected:true};
 const button={click(){
   clicks++;clickedAt=now;
   if(o.clickThrows)throw Error('synthetic click acknowledgement loss');
   userNodes=o.noUser?[]:[user(o.exact?prompt:'Attached text document')];
   if(o.duplicateUsers)userNodes.push(user('Second user turn'));
   assistantNodes=o.noAssistant?[]:[assistant(o.codeBlock?'Copy code\n'+response+'\nToolbar':response)];
   if(o.latestWrong)assistantNodes.push(assistant(JSON.stringify({request_id:OTHER,content:'OLD',tool_calls:[]})));
   editor.value=o.foreignDraft?'A new manual draft':'';
   location.pathname=o.unsavedRoute?'/':'/c/fixture';
   if(o.userIntervenes)listeners.keydown?.({isTrusted:true});
 }};
 const core={findEditor:()=>editor,readEditorRaw:e=>e.value,
   fillEditor(_doc,e,text){e.value=text;return true;},findSendButton:()=>button,
   ensureHigh:async()=>({ok:true,level:'High',verified:true}),
   selectedReasoning:()=>({ok:true,level:'High'}),
   detectAccountConflict:()=>!!(o.accountConflict&&clicks)};
 const win={};win.top=o.subframe?{}:win;
 const DateMock=class extends Date{static now(){return now;}};
 const context={window:win,location,document:doc,history:{replaceState(){}},
   ChatLaunchSmoke:core,Date:DateMock,console,
   setTimeout(fn,ms){now+=ms;queueMicrotask(fn);},
   chrome:{runtime:{async sendMessage(m){
     if(m.type==='hermes-relay-get')return {ok:true,prompt,completionMode:!!o.completionMode};
     if(m.type==='hermes-relay-intent'){intents++;if(o.loseIntentAck)throw Error('lost intent ACK');return {ok:true};}
     if(m.type==='hermes-relay-preflight')return {ok:!o.preflightDenied};
     if(m.type==='hermes-relay-stage'){stages.push(m.stage);return {ok:!(o.savedAckLost&&m.stage==='message_saved')};}
     if(m.type==='hermes-relay-finished'){final=m;return {ok:true};}
     return {ok:false};
   }}}};
 const hooked=source.replace('run().catch(', 'globalThis.__completion=run().catch(');
 assert.notEqual(hooked,source,'Expected one content run entry point');
 vm.runInNewContext(hooked,context);
 if(context.__completion)await context.__completion;
 return {clicks,intents,final,stages,draft:editor.value};
}

function notCommitted(s){assert.equal(s.clicks,1);assert.equal(s.intents,1);
 assert.equal(s.stages.includes('message_saved'),false);assert.notEqual(s.final?.status,'done');}
function completed(s){assert.equal(s.clicks,1);assert.equal(s.intents,1);
 assert.equal(s.stages.filter(x=>x==='message_saved').length,1);assert.equal(s.final.status,'done');
 assert.equal(JSON.parse(s.final.responseText).request_id,ID);}
const cases=[
 ['exact authored prompt retains the existing path',async()=>completed(await scenario({exact:true}))],
 ['large tile with bound final JSON is accepted once',async()=>completed(await scenario())],
 ['short tile with bound final JSON is accepted once',async()=>completed(await scenario({short:true}))],
 ['unbound tile never creates a saved receipt',async()=>notCommitted(await scenario({noAssistant:true}))],
 ['wrong request ID never creates a saved receipt',async()=>notCommitted(await scenario({wrongId:true}))],
 ['malformed response envelope never creates a saved receipt',async()=>notCommitted(await scenario({invalidTools:true}))],
 ['generating response cannot confirm an attachment',async()=>notCommitted(await scenario({generating:true}))],
 ['an earlier transcript disables the attachment fallback',async()=>notCommitted(await scenario({preexistingTranscript:true}))],
 ['multiple authored user turns disable fallback',async()=>notCommitted(await scenario({duplicateUsers:true}))],
 ['latest wrong assistant is not replaced by an earlier matching answer',async()=>notCommitted(await scenario({latestWrong:true}))],
 ['assistant preceding the user cannot confirm the user',async()=>notCommitted(await scenario({assistantBefore:true}))],
 ['disconnected transcript nodes are not evidence',async()=>notCommitted(await scenario({disconnected:true}))],
 ['trusted user intervention disables fallback',async()=>notCommitted(await scenario({userIntervenes:true}))],
 ['new manual editor text disables fallback',async()=>notCommitted(await scenario({foreignDraft:true}))],
 ['new-chat route alone does not confirm persistence',async()=>notCommitted(await scenario({unsavedRoute:true}))],
 ['missing authored user node does not confirm persistence',async()=>notCommitted(await scenario({noUser:true}))],
 ['code block extraction binds the actual JSON without toolbar text',async()=>completed(await scenario({codeBlock:true}))],
 ['exact text with no assistant may be saved but cannot finish',async()=>{const s=await scenario({exact:true,noAssistant:true});assert.equal(s.stages.includes('message_saved'),true);assert.equal(s.final.status,'unknown');assert.equal(s.final.reason,'assistant_timeout');assert.equal(s.clicks,1);}],
 ['lost saved ACK remains uncertain without a second Send',async()=>{const s=await scenario({savedAckLost:true});assert.equal(s.clicks,1);assert.equal(s.final.status,'unknown');assert.equal(s.final.reason,'uncertain_send');}],
 ['lost intent ACK uses read-only proof and only one click',async()=>completed(await scenario({loseIntentAck:true}))],
 ['denied intent proof prevents the only click',async()=>{const s=await scenario({preflightDenied:true});assert.equal(s.clicks,0);assert.equal(s.final.reason,'uncertain_send');}],
 ['account conflict stops completion',async()=>{const s=await scenario({accountConflict:true});assert.equal(s.final.reason,'account_context_changed');assert.equal(s.stages.includes('message_saved'),false);}],
 ['initial manual draft is preserved without Send',async()=>{const s=await scenario({initialDraft:'Owner draft'});assert.equal(s.clicks,0);assert.equal(s.draft,'Owner draft');assert.equal(s.final.reason,'manual_input');}],
 ['click exception never permits another click',async()=>{const s=await scenario({clickThrows:true});assert.equal(s.clicks,1);assert.equal(s.final.reason,'content_script_error');}],
 ['late unbound evidence does not extend the existing timeout',async()=>{const s=await scenario({answerDelay:80000});notCommitted(s);assert.equal(s.final.reason,'user_message_not_confirmed');}],
 ['completion mode still requires the external background ACK lifecycle',async()=>{const s=await scenario({completionMode:true});assert.equal(s.clicks,1);assert.equal(s.final,null);assert.equal(s.stages.includes('message_saved'),true);}],
 ['subframes do not inject a runner or send',async()=>{const s=await scenario({subframe:true});assert.equal(s.clicks,0);assert.equal(s.final,null);}]
];
(async()=>{
 let failed=0;const results=[];
 for(const [name,run] of cases){try{await run();results.push({name,pass:true});}catch(error){failed++;results.push({name,pass:false,error:String(error.message)});}}
 console.log(JSON.stringify({tests:cases.length,passed:cases.length-failed,failed,
   real_browser_used:false,real_model_requests:0,real_telegram_sends:0,results},null,2));
 process.exitCode=failed?1:0;
})();
