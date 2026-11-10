"""Native Edge MV3 tests of owned-draft quarantine; intercepted pages and fake Relay only."""
import asyncio,json,tempfile,threading,time,hashlib,sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from playwright.async_api import async_playwright
R=Path(__file__).resolve().parent
ROOT=R.parents[1]
CORE=ROOT.parent/"chat-launch-smoke-desktop-test/web-companion/core.js"
A="11111111-1111-4111-8111-111111111111";B="22222222-2222-4222-8222-222222222222"
OLD='Synthetic old owned prompt {"request_id":"'+A+'"}\n[HR-'+A+']'
NEW='Synthetic NEW prompt {"request_id":"'+B+'"}\n[HR-'+B+']'
KEY="synthetic-test-key-"+("x"*40)
class State:
 def __init__(self):self.state="queued";self.tab=None;self.reason=None;self.result_posts=0;self.intents=0
def handler_for(state):
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def respond(self,obj):
   data=json.dumps(obj).encode();self.send_response(200)
   self.send_header("Content-Type","application/json");self.send_header("Access-Control-Allow-Origin","*")
   self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
  def do_OPTIONS(self):
   self.send_response(204);self.send_header("Access-Control-Allow-Origin","*")
   self.send_header("Access-Control-Allow-Headers","Authorization,Content-Type")
   self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS");self.end_headers()
  def do_GET(self):
   self.respond({"data":[{"id":"web-chatgpt-high"}]})
  def do_POST(self):
   if self.headers.get("Authorization")!="Bearer "+KEY:self.send_error(403);return
   body=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
   if self.path=="/bridge/claim":
    job={"id":B,"lease":"synthetic-new-lease","prompt":NEW} if state.state=="queued" else None
    if job:state.state="claimed"
    self.respond({"ok":True,"job":job});return
   if self.path=="/bridge/event":
    assert body["id"]==B and body["lease"]=="synthetic-new-lease"
    action=body["action"]
    if action=="intent":state.state="intent";state.tab=body["tab"];state.intents+=1
    elif action=="saved":state.state="saved"
    elif action=="result":
     answer=json.loads(body["text"]);assert answer["request_id"]==B
     assert answer["content"]=="DRAFT_RECOVERY_FIXTURE_OK"
     state.state="done";state.result_posts+=1
    elif action=="blocked":
     state.reason=body.get("reason");state.state="uncertain" if state.state in ("intent","saved") else "blocked"
    self.respond({"ok":True,"state":state.state,"tab":state.tab});return
   self.send_error(404)
 return Handler
def fixture_html(initial):
 return """<!doctype html><html><body><form><div id="prompt-textarea" class="ProseMirror" contenteditable="true" role="textbox"></div><button type="button" data-testid="send-button" aria-label="Send prompt">Send</button></form><script>
 const editor=document.getElementById("prompt-textarea");editor.textContent=INITIAL;
 document.querySelector("button").onclick=()=>{
  const text=editor.innerText;
  const user=document.createElement("div");user.dataset.messageAuthorRole="user";user.textContent=text;document.body.appendChild(user);
  editor.textContent="";
  history.replaceState(null,"","/c/22222222-2222-4222-8222-222222222222");
  const assistant=document.createElement("div");assistant.dataset.messageAuthorRole="assistant";
  const pre=document.createElement("pre"),code=document.createElement("code");
  code.textContent=JSON.stringify({request_id:"22222222-2222-4222-8222-222222222222",content:"DRAFT_RECOVERY_FIXTURE_OK",tool_calls:[]});
  pre.appendChild(code);assistant.appendChild(pre);document.body.appendChild(assistant);
  document.querySelector("button").disabled=true;
 };
 </script></body></html>""".replace("INITIAL",json.dumps(initial).replace("<","\\u003c"))
async def run_case(p,kind):
 state=State();server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(state))
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 endpoint="http://127.0.0.1:"+str(server.server_port)
 try:
  with tempfile.TemporaryDirectory(prefix="hermes-owned-draft-") as folder:
   folder=Path(folder);ext=folder/"extension";ext.mkdir()
   source=(R/"hermes_relay_bg.js").read_text(encoding="utf8")
   assert source.count('"http://127.0.0.1:18765"')==1
   (ext/"background.js").write_text(source.replace('"http://127.0.0.1:18765"',json.dumps(endpoint)),encoding="utf8")
   (ext/"hermes_relay_content.js").write_bytes((R/"hermes_relay_content.js").read_bytes())
   (ext/"core.js").write_bytes(CORE.read_bytes())
   (ext/"popup.html").write_text("<html><body>Offline test controller</body></html>")
   (ext/"hermes-relay-config.json").write_text(json.dumps({"endpoint":endpoint,"key":KEY}))
   (ext/"manifest.json").write_text(json.dumps({"manifest_version":3,"version":"1.0","name":"Offline owned draft fixture","permissions":["storage","tabs","alarms","scripting"],"host_permissions":[endpoint+"/*","https://chatgpt.com/*"],"background":{"service_worker":"background.js"}}))
   context=await p.chromium.launch_persistent_context(str(folder/"profile"),channel="msedge",headless=True,args=["--no-first-run","--no-default-browser-check","--disable-background-networking","--disable-component-update","--host-resolver-rules=MAP chatgpt.com ~NOTFOUND","--disable-extensions-except="+str(ext),"--load-extension="+str(ext)])
   try:
    initial=OLD if kind!="user_draft" else "UNTOUCHED_MANUAL_FIXTURE"
    network={"fulfilled":0,"outside":0}
    async def guard(route):
     if route.request.url.startswith("https://chatgpt.com/"):
      network["fulfilled"]+=1
      await route.fulfill(status=200,content_type="text/html",body=fixture_html(initial))
     elif route.request.url.startswith(endpoint+"/") or route.request.url.startswith("chrome-extension:"):
      await route.continue_()
     else:
      network["outside"]+=1
      await route.abort()
    await context.route("**/*",guard)
    worker=context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker",timeout=15000)
    extension_id=worker.url.split("/")[2]
    panel=await context.new_page()
    await panel.goto("chrome-extension://"+extension_id+"/popup.html?panel=1",wait_until="domcontentloaded",timeout=15000)
    await worker.evaluate("""async ({a,old,terminalState})=>{
     const win=await chrome.windows.getLastFocused();
     await chrome.storage.local.set({["hermesRelayTerminal_v1_"+a]:{
      schema:"hermes-terminal-v1",noReplay:true,retiredAt:1,server:{state:terminalState,tab:null},
      task:{job:{id:a,lease:"synthetic-old-lease",prompt:old},windowId:win.id,tabId:17,documentId:"old-document",state:terminalState,reason:"send_button_missing"}}});
    }""",{"a":A,"old":OLD,"terminalState":"uncertain" if kind=="uncertain_old" else "blocked"})
    enabled=await panel.evaluate("() => chrome.runtime.sendMessage({type:'hermes-relay-enable'})")
    assert enabled["ok"] is True
    # Hydrate a controlled DOM after the extension-owned native tab exists.
    # Chromium may attach its DevTools target after that tab's first request.
    # DNS is disabled for the real host; this never loads real ChatGPT.
    ready_until=time.monotonic()+12
    while time.monotonic()<ready_until:
     tab=await worker.evaluate("""async ()=>{
      const task=(await chrome.storage.local.get("hermesRelayActive_v1")).hermesRelayActive_v1;
      return task?.state==="preparing"&&task.documentId?{id:task.tabId,documentId:task.documentId}:null;
     }""")
     if tab:break
     await asyncio.sleep(.05)
    assert tab,"fixture_owned_tab_missing"
    await worker.evaluate("""async ({tab,initial,b})=>{
     await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[tab.documentId]},func:({initial,b})=>{
      // Explicit headless fixture: native tab.active remains false; expose its
      // expected inactive document visibility to the content runner as well.
      Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>"hidden"});
      const form=document.createElement("form"),editor=document.createElement("div");
      editor.id="prompt-textarea";editor.className="ProseMirror";
      editor.contentEditable="true";editor.setAttribute("role","textbox");
      editor.style.minHeight="40px";editor.textContent=initial;
      const button=document.createElement("button");button.type="button";
      button.dataset.testid="send-button";button.setAttribute("aria-label","Send prompt");
      button.textContent="Send";form.append(editor,button);document.body.replaceChildren(form);
      button.onclick=()=>{
       const user=document.createElement("div");user.dataset.messageAuthorRole="user";
       user.textContent=editor.innerText;document.body.appendChild(user);editor.textContent="";
       history.replaceState(null,"","/c/"+b);
       const assistant=document.createElement("div");assistant.dataset.messageAuthorRole="assistant";
       const pre=document.createElement("pre"),code=document.createElement("code");
       code.textContent=JSON.stringify({request_id:b,content:"DRAFT_RECOVERY_FIXTURE_OK",tool_calls:[]});
       pre.appendChild(code);assistant.appendChild(pre);document.body.appendChild(assistant);button.disabled=true;
      };
     },args:[{initial,b}]});
    }""",{"tab":tab,"initial":initial,"b":B})
    started=time.monotonic();deadline=started+30
    while state.state not in ("done","blocked","uncertain") and time.monotonic()<deadline:await asyncio.sleep(.1)
    data=await worker.evaluate("""async ({a,b,old})=>{
     const all=await chrome.storage.local.get(null);
     const backups=Object.entries(all).filter(([k])=>k.startsWith("hermesDraftQuarantine_v1_"));
     return {backup_count:backups.length,backup_exact:backups.length===1&&backups[0][1].prompt===old,
      backup_no_replay:backups.length===1&&backups[0][1].noReplay===true,
      old_no_replay:all["hermesRelayTerminal_v1_"+a]?.noReplay===true,
      active_state:all.hermesRelayActive_v1?.state};
    }""",{"a":A,"b":B,"old":OLD})
    if kind=="owned_pre_send":
     if state.state!="done":
      debug=await worker.evaluate("""async ()=>{
       const task=(await chrome.storage.local.get("hermesRelayActive_v1")).hermesRelayActive_v1;
       const tab=task?.tabId;
       let dom=null;
       if(tab)try{
        const result=await chrome.scripting.executeScript({target:{tabId:tab},func:()=>{
         const c=globalThis.ChatLaunchSmoke,e=c?.findEditor(document);
         return {origin:location.origin,editables:document.querySelectorAll("[contenteditable]").length,prompt:!!document.querySelector("#prompt-textarea"),forms:document.forms.length,bodyTags:[...document.body.children].slice(0,8).map(n=>n.tagName),core:!!c,runner:globalThis.__HermesRelayContent===true,ready:document.readyState,path:location.pathname,visible:document.visibilityState,editor:!!e,editorReason:c?.editorFailureReason?.(document),chars:e?c.readEditorRaw(e).length:null};
        }});dom=result[0]?.result;
       }catch(_){dom={probe_failed:true};}
       return {state:task?.state,reason:task?.reason,bootstrap:task?.bootstrapProtocol,files:task?.bootstrapFileAttempts,dom};
      }""")
      print(json.dumps({"fixture_debug":debug,"server_state":state.state,"network":network}),flush=True)
     assert state.state=="done","owned_recovery_not_done:"+str(state.reason)
     assert state.result_posts==1 and state.intents==1,"wrong_send_count"
     assert data["backup_count"]==1 and data["backup_exact"] and data["backup_no_replay"],"durable_backup_not_verified"
    else:
     assert state.state=="blocked" and state.reason=="manual_input","unowned_or_uncertain_draft_not_blocked:"+str(state.reason)
     assert state.intents==0 and state.result_posts==0 and data["backup_count"]==0,"manual_or_uncertain_draft_changed"
     targets=[page for page in context.pages if page.url.startswith("https://chatgpt.com")]
     assert len(targets)==1
     preserved=await worker.evaluate("""async tab=>{
      const r=await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[tab.documentId]},func:()=>{
       const c=globalThis.ChatLaunchSmoke,e=c.findEditor(document);
       return {raw:c.readEditorRaw(e),text:e.textContent};
      }});return r[0].result;
     }""",tab)
     assert preserved["raw"]==initial or preserved["text"]==initial,"manual_draft_changed:"+kind+":"+str(preserved)
    assert data["old_no_replay"] is True
    return {"case":kind,"ok":True,"state":state.state,"reason":state.reason,"intents":state.intents,"results":state.result_posts,"backup_count":data["backup_count"],"elapsed_seconds":round(time.monotonic()-started,2)}
   finally:await context.close()
 finally:server.shutdown();server.server_close()
async def main():
 async with async_playwright() as p:
  results=[]
  for kind in ("owned_pre_send","user_draft","uncertain_old"):
   result=await run_case(p,kind);results.append(result);print(json.dumps(result),flush=True)
  print(json.dumps({"ok":True,"environment":"native Edge MV3 temporary profiles with intercepted web pages and fake HTTP","cases":results,"hashes":{name:hashlib.sha256((R/name).read_bytes()).hexdigest() for name in ("hermes_relay_bg.js","hermes_relay_content.js")},"real_chatgpt_sends":0,"telegram_messages":0,"user_browser_changed":False}),flush=True)
if __name__=="__main__":
 try:asyncio.run(main())
 except Exception as exc:
  import traceback
  print(json.dumps({"ok":False,"error_type":type(exc).__name__,"reason":str(exc)[:200],"line":traceback.extract_tb(exc.__traceback__)[-1].lineno}),flush=True)
  raise SystemExit(1)

