"""Yandex DOM canary for the no-toggle default Chat composer.
Real browser binary, synthetic page only, no extension or user profile.
"""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright
HERE=Path(__file__).resolve().parent
EXE=Path(r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe")
def run():
  src=(HERE/"browser_ready.mjs").read_text(encoding="utf8")
  preamble=src.split("export async function ensureChatReady")[0]
  preamble=preamble.replace("export function inspectChatComposer", "function inspectChatComposer",1)
  preamble+="\nwindow.inspectHermes=inspectChatComposer;"
  page_html="""<!doctype html><html><body>
    <div id="prompt-textarea" contenteditable="true"
       data-placeholder="Ask anything"
       style="border:1px solid gray;min-height:80px;width:400px"></div>
    <button id="send">Send</button><output id="sent">0</output>
    <script>document.getElementById('send').onclick=()=>{document.getElementById('sent').textContent='1'}</script>
  </body></html>"""
  out={}
  with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=str(EXE),headless=True,
      args=["--no-first-run","--no-default-browser-check"])
    try:
      context=browser.new_context()
      context.route("**/*",lambda route:route.fulfill(status=200,body=page_html,
        content_type="text/html") if route.request.url=="https://chatgpt.com/"
        else route.abort("blockedbyclient"))
      page=context.new_page()
      page.goto("https://chatgpt.com/",wait_until="load")
      page.add_script_tag(content=preamble)
      def scan(placeholder):
        page.locator("#prompt-textarea").evaluate(
          "(el,value)=>el.setAttribute('data-placeholder',value)",placeholder)
        return page.evaluate("window.inspectHermes({switchToChat:false})")
      chat=scan("Ask anything")
      work=scan("Work with ChatGPT")
      unknown=scan("Type here")
      out={
        "real_yandex_binary":True,"synthetic_page":True,
        "chat_without_toggle_ready":chat.get("state")=="READY" and chat.get("chatModeConfirmed") is True,
        "work_without_toggle_blocked":work.get("state")=="WORK_MODE",
        "unknown_without_toggle_blocked":unknown.get("state")=="MODE_CONTROL_NOT_VERIFIED",
        "no_send":page.locator("#sent").inner_text()=="0",
        "tabs_open":len(context.pages)
      }
      context.close()
    finally:browser.close()
  print(json.dumps(out,sort_keys=True))
  return all((out["chat_without_toggle_ready"],out["work_without_toggle_blocked"],
              out["unknown_without_toggle_blocked"],out["no_send"],out["tabs_open"]==1))
if __name__=="__main__":sys.exit(0 if run() else 2)
