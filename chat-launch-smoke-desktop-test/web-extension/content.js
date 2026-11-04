(() => {
  "use strict";
  const core = globalThis.ChatLaunchSmoke;
  if (!core || !core.shouldRun(window.location) || window.top !== window) return;
  // Strip the signal before acting: navigation/reloading can't replay the request.
  history.replaceState(history.state, "", location.pathname + location.search);
  const banner = document.createElement("div");
  banner.id = "chat-launch-smoke-banner";
  Object.assign(banner.style, {
    position:"fixed",zIndex:"2147483647",right:"16px",bottom:"16px",
    background:"#16202b",color:"white",font:"13px/1.4 system-ui,sans-serif",
    padding:"12px 16px",borderRadius:"10px",maxWidth:"330px",
    boxShadow:"0 4px 22px rgba(0,0,0,.23)"
  });
  banner.textContent = "Тест создания чата: ищу поле ввода…";
  document.body.appendChild(banner);
  const setStatus = (s) => {banner.textContent=s;};
  async function waitFor(fn, ms=16000) {
    const until=Date.now()+ms;
    while(Date.now()<until) {
      const x=fn();
      if(x) return x;
      await new Promise(resolve => setTimeout(resolve,250));
    }
    return null;
  }
  async function go() {
    const editor = await waitFor(()=>core.findEditor(document),12000);
    if (!editor) {
      const d = core.diagnostics(document);
      setStatus('Редактор ChatGPT не найден (редактируемых: '+d.editables+', полей textbox: '+d.textboxes+', DOM-деревьев: '+d.roots+'). Ты уже вошёл в ChatGPT; изменилась разметка редактора.');
      return;
    }
    if (!core.fillEditor(editor.ownerDocument || document,editor,core.PROMPT)) {
      setStatus("Не удалось заполнить поле ввода: интерфейс ChatGPT изменился.");
      return;
    }
    const send = await waitFor(()=>core.findSendButton(document),6000);
    if (!send) {
      setStatus("Промпт введён, но кнопка отправки не найдена. Отправь вручную.");
      return;
    }
    // One send on this explicit link activation. No retries, no repeated sends.
    send.click();
    setStatus("Один запрос отправлен через интерфейс. Проверь новый чат и ответ «ПРИНЯТО».");
  }
  go().catch((e)=>setStatus("Ошибка теста: "+String(e.message||e)));
})();
