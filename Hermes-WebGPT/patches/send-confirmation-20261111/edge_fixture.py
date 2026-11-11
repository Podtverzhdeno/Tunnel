"""Real Edge DOM, isolated context and intercepted HTML only; no model or Telegram."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
SOURCE = (HERE / 'hermes_relay_content.js').read_text(encoding='utf8')
ID = '00000000-0000-4000-8000-000000000001'
HTML = '<!doctype html><html><body><main id="chat"></main><textarea id="editor"></textarea><button id="send" type="button">Send</button></body></html>'
SETUP = r'''o=>{
 const id=o.id, prompt=o.small?'Request '+id:'Request '+id+' '+'x'.repeat(13000);
 let clock=10000;const nativeTimeout=window.setTimeout.bind(window);
 window.setTimeout=(fn,ms)=>nativeTimeout(()=>{clock+=ms;fn();},0);
 const RealDate=window.Date;window.Date=class extends RealDate{static now(){return clock;}};
 window.__result={clicks:0,intents:0,stages:[],final:null};
 const editor=document.getElementById('editor'),button=document.getElementById('send'),chat=document.getElementById('chat');
 const turn=(role,text)=>{const n=document.createElement('div');n.dataset.messageAuthorRole=role;n.textContent=text;return n;};
 if(o.preexisting)chat.append(turn('user','Older turn'),turn('assistant','Older answer'));
 button.onclick=()=>{
  window.__result.clicks++;
  chat.replaceChildren(turn('user',o.exact?prompt:'Attached document'));
  if(!o.noAssistant)chat.append(turn('assistant',JSON.stringify({request_id:o.wrongId?'00000000-0000-4000-8000-000000000002':id,content:'READY',tool_calls:[]})));
  if(o.generating){const stop=document.createElement('button');stop.dataset.testid='stop-button';document.body.append(stop);}
  editor.value='';history.replaceState({},'', '/c/fixture');
 };
 window.ChatLaunchSmoke={findEditor:()=>editor,readEditorRaw:e=>e.value,
  fillEditor(_doc,e,text){e.value=text;return true;},findSendButton:()=>button,
  ensureHigh:async()=>({ok:true,level:'High',verified:true}),selectedReasoning:()=>({ok:true,level:'High'}),detectAccountConflict:()=>false};
 window.chrome={runtime:{async sendMessage(m){
  if(m.type==='hermes-relay-get')return {ok:true,prompt};
  if(m.type==='hermes-relay-intent'){window.__result.intents++;return {ok:true};}
  if(m.type==='hermes-relay-preflight')return {ok:true};
  if(m.type==='hermes-relay-stage'){window.__result.stages.push(m.stage);return {ok:true};}
  if(m.type==='hermes-relay-finished'){window.__result.final={status:m.status,reason:m.reason};return {ok:true};}
  return {ok:false};
 }}};
}'''


async def main():
    checks=[]
    intercepted=0
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(channel='msedge',headless=True,
            args=['--no-first-run','--disable-background-networking','--disable-component-update'])
        try:
            context=await browser.new_context(service_workers='block')
            async def guard(route):
                nonlocal intercepted
                intercepted+=1
                if route.request.resource_type=='document':
                    await route.fulfill(status=200,content_type='text/html',body=HTML)
                else:
                    await route.abort()
            await context.route('**/*',guard)
            cases=[('exact',{'exact':True},True),('short attachment',{'small':True},True),
                   ('large attachment',{},True),('foreign request',{'wrongId':True},False),
                   ('no assistant',{'noAssistant':True},False),('generation active',{'generating':True},False),
                   ('old transcript',{'preexisting':True},False)]
            for label,options,accept in cases:
                page=await context.new_page()
                page.set_default_timeout(15000)
                await page.goto('https://chatgpt.com/#hermes-relay='+ID,wait_until='domcontentloaded')
                await page.evaluate(SETUP,{'id':ID,**options})
                hooked=SOURCE.replace('run().catch(', 'globalThis.__completion=run().catch(')
                assert hooked!=SOURCE
                await page.add_script_tag(content=hooked)
                result=await page.evaluate('async()=>{await window.__completion;return window.__result;}')
                assert result['clicks']==1 and result['intents']==1, label
                if accept:
                    assert result['final']['status']=='done' and 'message_saved' in result['stages'],label
                else:
                    assert result['final']['status']!='done' and 'message_saved' not in result['stages'],label
                checks.append({'name':label,'pass':True})
                await page.close()
            await context.close()
        finally:
            await browser.close()
    print(json.dumps({'ok':True,'tests':len(checks),'passed':len(checks),'checks':checks,
        'browser':'real headless Edge, isolated context, synthetic intercepted HTML',
        'intercepted_document_requests':intercepted,'real_model_requests':0,'telegram_messages':0,
        'source_sha256':hashlib.sha256((HERE/'hermes_relay_content.js').read_bytes()).hexdigest()}))


if __name__=='__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({'ok':False,'error_type':type(error).__name__,'real_model_requests':0}))
        sys.exit(1)
