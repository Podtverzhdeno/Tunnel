"""Native Edge MV3 queue handoff test; synthetic HTTP and intercepted web pages."""
import asyncio,json,tempfile,threading,time,hashlib,sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from playwright.async_api import async_playwright
R=Path(__file__).resolve().parent
A="11111111-1111-4111-8111-111111111111"
B="22222222-2222-4222-8222-222222222222"
key="synthetic-fixture-key-"+("x"*40)
class State:
 result_at=None
 claims=[]
 events=[]
 claimed=False
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def respond(self,value):
  body=json.dumps(value).encode()
  self.send_response(200)
  self.send_header("Content-Type","application/json")
  self.send_header("Access-Control-Allow-Origin","*")
  self.send_header("Content-Length",str(len(body)))
  self.end_headers()
  self.wfile.write(body)
 def do_OPTIONS(self):
  self.send_response(204)
  self.send_header("Access-Control-Allow-Origin","*")
  self.send_header("Access-Control-Allow-Headers","Authorization,Content-Type")
  self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
  self.end_headers()
 def do_POST(self):
  if self.headers.get("Authorization")!="Bearer "+key:
   self.send_error(403);return
  b=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
  if self.path=="/bridge/event":
   State.events.append(b.get("action"))
   if b.get("action")=="result":
    State.result_at=time.monotonic();self.respond({"ok":True,"state":"done"});return
   self.respond({"ok":True,"state":"saved","tab":b.get("tab")});return
  if self.path=="/bridge/claim":
   now=time.monotonic()
   ready=State.result_at is not None and now-State.result_at>=0.35 and not State.claimed
   State.claims.append({"delay":None if State.result_at is None else now-State.result_at,"claimed":ready})
   if ready:State.claimed=True
   self.respond({"ok":True,"job":{"id":B,"lease":"synthetic-lease-b","prompt":"fixture"} if ready else None});return
  self.send_error(404)
async def main():
 source=(R/"hermes_relay_bg.js").read_text(encoding="utf8")
 sha=hashlib.sha256((R/"hermes_relay_bg.js").read_bytes()).hexdigest()
 server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
 endpoint="http://127.0.0.1:"+str(server.server_port)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  with tempfile.TemporaryDirectory(prefix="hermes-native-queue-") as temp:
   temp=Path(temp);ext=temp/"extension";ext.mkdir()
   assert source.count('"http://127.0.0.1:18765"')==1,"loopback_fixture_rewrite_not_unique"
   (ext/"worker.js").write_text(source.replace('"http://127.0.0.1:18765"',json.dumps(endpoint)),encoding="utf8")
   (ext/"hermes-relay-config.json").write_text(json.dumps({"endpoint":endpoint,"key":key}),encoding="utf8")
   (ext/"manifest.json").write_text(json.dumps({"manifest_version":3,"name":"Offline queue fixture","version":"1.0","permissions":["storage","tabs","alarms","scripting"],"host_permissions":[endpoint+"/*","https://chatgpt.com/*"],"background":{"service_worker":"worker.js"}}),encoding="utf8")
   async with async_playwright() as p:
    ctx=await p.chromium.launch_persistent_context(str(temp/"profile"),channel="msedge",headless=True,args=["--no-first-run","--no-default-browser-check","--disable-background-networking","--disable-component-update","--disable-extensions-except="+str(ext),"--load-extension="+str(ext)])
    try:
     async def guard(route):
      url=route.request.url
      if url=="https://chatgpt.com/c/fixture":
       await route.fulfill(status=200,content_type="text/html",body="<html><body>Offline bound result fixture</body></html>")
      elif url.startswith(endpoint+"/") or url.startswith("chrome-extension:"):
       await route.continue_()
      else:
       await route.abort()
     await ctx.route("**/*",guard)
     worker=ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker",timeout=15000)
     page=await ctx.new_page()
     await page.goto("https://chatgpt.com/c/fixture",wait_until="domcontentloaded",timeout=15000)
     result=await worker.evaluate("""async ({id})=>{
      const tabs=await chrome.tabs.query({url:"https://chatgpt.com/c/fixture"});
      if(tabs.length!==1)throw Error("fixture_tab_not_unique");
      const tab=tabs[0];
      const probe=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>true});
      const documentId=probe[0].documentId;
      if(!documentId)throw Error("native_document_id_missing");
      await chrome.storage.local.set({hermesRelayEnabled_v1:true,hermesRelayActive_v1:{
       job:{id,lease:"synthetic-lease-a",prompt:"fixture"},tabId:tab.id,
       windowId:tab.windowId,documentId,state:"saved",savedUrl:"https://chatgpt.com/c/fixture"}});
      return {tabId:tab.id,windowId:tab.windowId,documentId};
     }""",{"id":A})
     await worker.evaluate("""async ({id,tabId})=>{
      try{return await chrome.scripting.executeScript({target:{tabId},func:async id=>
       chrome.runtime.sendMessage({type:"hermes-relay-finished",id,status:"done",
        userSeen:true,assistantSeen:true,url:"https://chatgpt.com/c/fixture",responseText:"fixture"}),args:[id]});}
      catch(_){return {sender_document_closed:true};}
     }""",{"id":A,"tabId":result["tabId"]})
     deadline=time.monotonic()+8
     while time.monotonic()<deadline and not State.claimed:await asyncio.sleep(0.05)
     assert State.claimed,"following_request_not_claimed_within_warm_window"
     async def metadata():
      return await worker.evaluate("""async ()=>{
      const v=(await chrome.storage.local.get("hermesRelayActive_v1")).hermesRelayActive_v1;
      const tabs=await chrome.tabs.query({});return {id:v?.job?.id,windowId:v?.windowId,
       following:tabs.filter(t=>t.url?.includes("hermes-relay=")).map(t=>({active:t.active,windowId:t.windowId}))};
     }""")
     status=await metadata()
     while time.monotonic()<deadline and (status["id"]!=B or len(status["following"])!=1):
      await asyncio.sleep(0.05)
      status=await metadata()
     assert State.events.count("result")==1,"result_post_count_mismatch"
     delay=next(x["delay"] for x in State.claims if x["claimed"])
     assert delay<4,"warm_claim_exceeded_four_seconds"
     assert status["id"]==B and status["windowId"]==result["windowId"],"following_ownership_not_persisted"
     assert len(status["following"])==1 and status["following"][0]["active"] is False,"following_tab_not_inactive_or_missing"
     out={"ok":True,"environment":"real Edge MV3 in temporary profile, synthetic HTTP","source_sha256":sha,"fixture_change":"loopback port literal only","result_posts":1,"new_claims":1,"claim_delay_seconds":round(delay,3),"same_normal_window":True,"inactive_following_tab":True,"real_chatgpt_requests":0,"telegram_messages":0,"user_browser_modified":False}
     print(json.dumps(out),flush=True)
    finally:await ctx.close()
 finally:server.shutdown();server.server_close()
if __name__=="__main__":
 try:asyncio.run(main())
 except Exception as exc:
  print(json.dumps({"ok":False,"error_type":type(exc).__name__,"reason":str(exc)[:200],"line":__import__("traceback").extract_tb(exc.__traceback__)[-1].lineno}),flush=True)
  sys.exit(1)
