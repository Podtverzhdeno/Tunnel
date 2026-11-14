"""Actual installed Yandex binary; isolated synthetic ChatGPT-origin UI.
Evidence is DOM/reload/network only, NOT authorized ChatGPT or model E2E.
"""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[0]
EXE=Path(r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe")
PAGE="""<!doctype html><html><head><meta charset="utf-8"/>
<style>body{font:16px Arial;padding:20px}
#prompt-textarea{min-height:80px;width:500px;border:1px solid gray;white-space:pre-wrap}
</style></head><body>
<nav role="tablist"><button role="tab" aria-selected="false" id="chat">Чат</button>
<button role="tab" aria-selected="true" id="work">Работа</button></nav>
<div id="prompt-textarea" contenteditable="true" role="textbox"></div>
<div data-message-author-role="assistant">SYNTHETIC FIXTURE ANSWER</div>
<button id="send">Отправить</button><span id="sendCount">0</span>
<script>
const c=document.getElementById('chat'), w=document.getElementById('work');
c.addEventListener('click',()=>{
 c.setAttribute('aria-selected','true');w.setAttribute('aria-selected','false');
});
document.getElementById('send').addEventListener('click',()=>{
 document.getElementById('sendCount').textContent='1';
});
</script></body></html>"""
def run():
 out={"real_yandex_binary":False,"synthetic_page_only":True,
      "origin_verified":False,"dom_editor_ready":False,
      "work_mode_detected":False,"chat_mode_selected":False,
      "reload_recovery_verified":False,"offline_detected":False,
      "online_recovery_verified":False,"response_fixture_visible":False,
      "external_network_requests":0,"send_clicks":0,
      "max_open_tabs":0,"model_response_verified":False,
      "conversation_without_mode_blocks":False}
 script=(ROOT/"browser_ready.mjs").read_text(encoding="utf8").split(
       "export async function ensureChatReady")[0]
 script=script.replace("export function inspectChatComposer",
                       "function inspectChatComposer",1)
 script+="\nwindow.hermesInspectChatComposer=inspectChatComposer;\n"
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=str(EXE),headless=True,
     args=["--no-first-run","--no-default-browser-check"])
  try:
   context=browser.new_context()
   def route(r):
    if r.request.url.rstrip("/")=="https://chatgpt.com":
      r.fulfill(status=200,content_type="text/html;charset=utf-8",body=PAGE)
    else:
      out["external_network_requests"]+=1
      r.abort("blockedbyclient")
   context.route("**/*",route)
   page=context.new_page()
   out["real_yandex_binary"]=True
   out["max_open_tabs"]=len(context.pages)
   page.goto("https://chatgpt.com/",wait_until="load",timeout=15000)
   page.add_script_tag(content=script)
   out["origin_verified"]=page.evaluate("location.origin")== "https://chatgpt.com"
   first=page.evaluate("window.hermesInspectChatComposer({switchToChat:false})")
   out["work_mode_detected"]=first.get("state")=="WORK_MODE"
   switched=page.evaluate("window.hermesInspectChatComposer({switchToChat:true})")
   final=page.evaluate("window.hermesInspectChatComposer({switchToChat:false})")
   out["chat_mode_selected"]=switched.get("state")=="MODE_SWITCH_REQUESTED" and (
      final.get("state")=="READY" and final.get("chatModeConfirmed") is True)
   out["dom_editor_ready"]=final.get("editorEmpty") is True
   out["response_fixture_visible"]=page.locator(
     '[data-message-author-role="assistant"]').inner_text()=="SYNTHETIC FIXTURE ANSWER"
   context.set_offline(True)
   offline=page.evaluate("window.hermesInspectChatComposer({switchToChat:false})")
   out["offline_detected"]=offline.get("state")=="OFFLINE"
   context.set_offline(False)
   online=page.evaluate("window.hermesInspectChatComposer({switchToChat:false})")
   out["online_recovery_verified"]=online.get("state")=="READY"
   page.reload(wait_until="load",timeout=15000)
   page.add_script_tag(content=script)
   again=page.evaluate("window.hermesInspectChatComposer({switchToChat:true})")
   recover=page.evaluate("window.hermesInspectChatComposer({switchToChat:false})")
   out["reload_recovery_verified"]=again.get("state")=="MODE_SWITCH_REQUESTED" and (
      recover.get("state")=="READY")
   page.evaluate("""() => {
     history.pushState({},'', '/c/synthetic-conversation');
     document.querySelector('nav[role="tablist"]').remove();
   }""")
   no_mode=page.evaluate(
     "window.hermesInspectChatComposer({switchToChat:false})")
   out["conversation_without_mode_blocks"]=(
     no_mode.get("state")=="MODE_CONTROL_NOT_VERIFIED")
   out["max_open_tabs"]=max(out["max_open_tabs"],len(context.pages))
   out["send_clicks"]=int(page.locator("#sendCount").inner_text())
   context.close()
  finally:browser.close()
 return out
if __name__=="__main__":
 result=run()
 print(json.dumps(result,sort_keys=True,ensure_ascii=True))
 assert result["max_open_tabs"]==1 and result["send_clicks"]==0
 keys=("real_yandex_binary","origin_verified","dom_editor_ready",
       "work_mode_detected","chat_mode_selected","reload_recovery_verified",
       "offline_detected","online_recovery_verified","response_fixture_visible",
       "conversation_without_mode_blocks")
 if not all(result[k] for k in keys):sys.exit(2)
