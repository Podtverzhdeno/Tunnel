"use strict";
const state=document.getElementById("state");
const circuit=document.getElementById("circuit");
const resume=document.getElementById("resume");
Promise.all([
  chrome.storage.local.get(["chatSmokeLast","chatSmokeAuthPaused"]),
  chrome.storage.session.get("chatSmokeCircuit")
]).then(([last,active])=>{
  const v=last.chatSmokeLast;
  const date=typeof v?.updatedAt==="number"?
    new Date(v.updatedAt).toLocaleString():"";
  state.textContent=v?
    "Задание: "+(v.id||"—")+"\nСтадия: "+v.stage+"\nВремя: "+date:
    "Новых заданий пока нет.";
  state.style.whiteSpace="pre-wrap";
  const c=active.chatSmokeCircuit||{failures:0,paused:false};
  const authPaused=last.chatSmokeAuthPaused===true;
  const remaining=Math.max(0,Math.ceil(((c.cooldownUntil||0)-Date.now())/1000));
  circuit.textContent=authPaused?
    "Обнаружен конфликт учётной записи ChatGPT. Очередь остановлена до ручной проверки аккаунта.":
    c.paused?"Очередь остановлена: "+(c.stopReason||"требуется проверка")+
      ". Неизвестные отправки не повторяются автоматически.":
    remaining>0?"Временная пауза интерфейса: "+remaining+
      " сек. После неё разрешена только одна новая проверочная задача.":
    c.probeId?"Проверочная задача ожидает подтверждения сохранённого сообщения. Новые задания временно не выдаются.":
    "Очередь активна; ошибок интерфейса подряд: "+(c.failures||0);
  resume.disabled=!c.paused&&!authPaused;
}).catch(()=>{
  state.textContent="Не удалось получить локальную диагностику.";
  resume.disabled=true;
});
resume.addEventListener("click",async()=>{
  resume.disabled=true;
  try{
    const outcome=await chrome.runtime.sendMessage({type:"chat-smoke-resume"});
    circuit.textContent=outcome?.ok?
      "Очередь возобновлена вручную.":"Возобновление не подтверждено.";
  }catch(_){
    circuit.textContent="Ошибка связи с фоновым обработчиком.";
  }
});