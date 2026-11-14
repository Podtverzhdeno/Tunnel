"""Real chatgpt.com read-only Yandex navigation, disposable browser context.
Never uses user profile, never submits/edits, no prompt/user content emitted.
"""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright
EXE=Path(r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe")
def smoke():
 result={"browser":"Yandex","profile":"disposable","real_chatgpt_origin":False,
  "dom_loaded":False,"composer_visible":False,"chat_work_controls":False,
  "sign_in_visible":False,"send_clicks":0,"model_answer_verified":False,
  "navigation_error_type":None,"browser_http_status":None}
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=str(EXE),headless=True,
    args=["--no-first-run","--no-default-browser-check"])
  try:
   ctx=browser.new_context()
   page=ctx.new_page()
   try:
    response=page.goto("https://chatgpt.com/",wait_until="domcontentloaded",timeout=25000)
    result["browser_http_status"]=response.status if response else None
    result["real_chatgpt_origin"]=page.url.startswith("https://chatgpt.com")
    result["dom_loaded"]=page.locator("body").count()>0
    inspect=page.evaluate("""() => {
      const displayed=el=>!!el&&!!el.getClientRects().length;
      const input=[...document.querySelectorAll('#prompt-textarea,[data-testid="composer-input"]')]
        .some(displayed);
      const words=[...document.querySelectorAll('button,[role="tab"]')]
        .filter(displayed)
        .map(x=>(x.getAttribute('aria-label')||x.textContent||'').trim().toLowerCase());
      const chat=words.some(x=>x==='chat'||x==='чат');
      const work=words.some(x=>x==='work'||x==='работа');
      const login=[...document.querySelectorAll('a,button')].filter(displayed)
        .some(el=>/^(войти|log in|sign in)$/i.test((el.textContent||'').trim()));
      return {input,chatAndWork:chat&&work,login};
    }""")
    result["composer_visible"]=bool(inspect["input"])
    result["chat_work_controls"]=bool(inspect["chatAndWork"])
    result["sign_in_visible"]=bool(inspect["login"])
   except Exception as e:
    result["navigation_error_type"]=type(e).__name__
   finally:ctx.close()
  finally:browser.close()
 return result
if __name__=="__main__":
 print(json.dumps(smoke(),ensure_ascii=True))
