const $=s=>document.querySelector(s);
const status=$('#message'),summary=$('#summary'),slots=$('#slots'),assignment=$('#assignment'),diagnosticOutput=$('#diagnostic-output'),queue=$('#queue');
let selectedLease=null;
const talk=msg=>new Promise((resolve,reject)=>chrome.runtime.sendMessage(msg,res=>{
 if(chrome.runtime.lastError)return reject(new Error(chrome.runtime.lastError.message));
 if(!res?.ok)return reject(new Error(res?.error??'Unknown error'));resolve(res.result);
}));
const message=text=>{status.textContent=text;};
const explain={
 REACHABLE:'отвечает',
 AUTH_REQUIRED:'доступен, требуется авторизация',
 TIMEOUT:'тайм-аут',
 UNREACHABLE_OR_BLOCKED:'нет связи или доступ запрещён',
 UNEXPECTED_HTTP_STATUS:'неожиданный HTTP-статус',
 UNEXPECTED_HEALTH_RESPONSE:'неожиданный формат ответа'
};
$('#companion-probe').addEventListener('click',async()=>{
 const button=$('#companion-probe'),output=$('#companion-status');
 button.disabled=true;output.textContent='Проверяю локальные сервисы без отправки сообщений...';
 try{
  const state=await talk({type:'COMPANION_PROBE'});
  if(state.schema!==1||!Array.isArray(state.services)||state.sendEnabled!==false)throw Error('Unexpected probe contract');
  output.textContent=state.services.map(s=>
   `${s.label}: ${explain[s.state]??'неизвестный статус'}${s.httpStatus===null?'':` (HTTP ${s.httpStatus})`}`
  ).join('\n')+'\nЭто только проверка доступности, не подтверждение работы агентов.';
 }catch(e){output.textContent='Ошибка диагностики: '+String(e.message);}
 finally{button.disabled=false;}
});

$('#relay-capacity-probe').addEventListener('click',async()=>{
 const button=$('#relay-capacity-probe'),output=$('#relay-capacity-status');
 button.disabled=true;
 output.textContent='Проверяю реальные слоты Relay (без отправки сообщений)...';
 try{
  const state=await talk({type:'RELAY_CAPACITY_PROBE'});
  if(state?.state==='OBSERVED'&&state.mode==='READ_ONLY_CAPACITY'&&state.sendEnabled===false){
   output.textContent=`Hermes Relay: максимум ${state.maxActiveRequests} запросов одновременно; занято ${state.activeRequests}, свободно ${state.availableNow}.
Неопределённых в журнале: ${state.uncertainTotal}; заблокированных: ${state.blockedTotal}; в очереди: ${state.queued}.
Рекомендация для Relay: не более ${state.advisoryMaxParallelism} параллельных запросов (это НЕ лимит вкладок).
Данные агрегированные, не подтверждают владение отдельной задачей. Автоотправка отключена.`;
  }else{
   const known={SIDECAR_UNAVAILABLE:'Сервис диагностики ёмкости недоступен',
     RELAY_UNAVAILABLE:'Relay недоступен или не подтвердил состояние',
     UNEXPECTED_HTTP_STATUS:'Неожиданный статус диагностики',
     INVALID_RESPONSE:'Повреждённый ответ диагностики'};
   output.textContent=`${known[state?.state]??'Неизвестное состояние'}; лимит не проверен. Работа с реальными отправками не включается.`;
  }
 }catch(e){output.textContent='Ошибка проверки ёмкости: '+String(e.message);}
 finally{button.disabled=false;}
});

$('#turn-preview').addEventListener('click',async()=>{
 const output=$('#turn-preview-status'),button=$('#turn-preview');
 if(!selectedLease){output.textContent='Сначала нажми «Показать задание» в карточке исполнителя.';return;}
 button.disabled=true;
 try{
  const preview=await talk({type:'HANDOFF_PREVIEW',...selectedLease});
  if(preview?.schema!=='autopilot-relay-turn-preview-v1'||preview.state!=='PREVIEW_ONLY'||
      preview.sendEnabled!==false||preview.relayBound!==false)throw Error('Неверный контракт');
  output.textContent=`Логический turn_id: ${preview.turnId}
Задание: ${preview.taskId} · Агент: ${preview.workerId} · Epoch: ${preview.epoch}
Это предварительный идентификатор. Relay ещё НЕ принял запрос и НЕ выдал lease; отправка отключена.`;
 }catch(e){output.textContent='Не удалось подготовить привязку: '+String(e.message);}
 finally{button.disabled=false;}
});

function render(data){
 const run=data?.run;
 if(!run){selectedLease=null;summary.textContent='Нет запуска';queue.textContent='Очередь не запущена';slots.replaceChildren();for(const id of ['retry','pause','resume','export','diagnostics','timeline','stop','apply-concurrency','approve','watchdog','draft-all'])$('#'+id).disabled=true;$('#start').disabled=false;return;}
 const done=run.tasks.filter(t=>t.status==='VERIFIED').length;
 const approved=run.tasks.filter(t=>t.status==='OWNER_APPROVED').length;
 summary.textContent=`Запуск: ${run.id}\nСостояние: ${run.status} · ревизия: ${run.revision}\nПроверено независимо: ${done}/${run.tasks.length} · Принято владельцем: ${approved}`;
 const q=data.insights;queue.textContent=q?`Очередь: готово ${q.counts.READY}, зависимость ${q.counts.WAITING_DEPENDENCIES}, ресурс ${q.counts.RESOURCE_LOCKED}, заблокировано ${q.counts.BLOCKED}\nЗанято слотов: ${q.occupied}/${q.maxConcurrent} · Причина простоя: ${q.idleReason}`:'Нет данных по очереди';
 const advice=data.capacityAdvice;if(advice){const text=advice.proposed===advice.current?'Лимит пока сохранять':`Рекомендованный лимит: ${advice.proposed} (сейчас ${advice.current})`;queue.textContent+=`\n${text} · ${advice.reason} · предложение, не автопереключение`;}
 $('#max-concurrent').value=String(run.maxConcurrent??5);
 summary.style.whiteSpace='pre-wrap';slots.replaceChildren();
 for(const s of run.slots){
  const el=document.createElement('div');el.className='slot';
  const badge=document.createElement('span');badge.className='n';badge.textContent=s.id.split('-')[1];
  const rest=document.createElement('div');rest.className='slot-content';
  const title=document.createElement('div');title.textContent=`${run.status==='STOPPED'?'STOPPED':s.phase} · ${s.taskId??'без задачи'}`;
  const small=document.createElement('div');small.className='detail';small.textContent=`Tab: ${s.tabId??'—'} · Epoch: ${s.epoch} · Чекпоинт: ${s.checkpointVersion} · Причина: ${q?.slotReasons?.find(x=>x.workerId===s.id)?.reason??s.phase}`;
  rest.append(title,small);
  if(s.monitor?.level&&s.monitor.level!=='OK'){const warn=document.createElement('div');warn.className='detail';warn.textContent=`Watchdog: ${s.monitor.level} (проверь прогресс вручную)`;rest.append(warn);}
  if(s.phase==='MISSING_TAB'&&s.taskId){
   const recover=document.createElement('button');recover.type='button';recover.className='secondary';recover.textContent='Вернуть после сверки истории';
   recover.addEventListener('click',async()=>{
    const note=$('#approval-note').value.trim();if(!note){message('Опиши результат ручной сверки истории ChatGPT и незавершённых отправок.');return;}
    try{message('Проверяю отсутствие прежней вкладки…');await talk({type:'RECOVER_MISSING',workerId:s.id,taskId:s.taskId,epoch:s.epoch,note});
     $('#approval-note').value='';await refresh();message('Задача возвращена в очередь после подтверждённой вручную сверки. Отправка не выполнялась.');}
    catch(e){message(`Восстановление не выполнено: ${e.message}`);}
   });rest.append(recover);
  }else if(s.taskId){const task=run.tasks.find(t=>t.id===s.taskId);
   if(task){const view=document.createElement('button');view.type='button';view.className='secondary';view.textContent='Показать задание';
    view.addEventListener('click',()=>{selectedLease={workerId:s.id,taskId:s.taskId,epoch:s.epoch};$('#approval-note').value='';$('#approve').disabled=run.status!=='ACTIVE'||s.phase!=='WORKING';assignment.value=`${task.id}\n\n${task.prompt}\n\nКритерии приёмки:\n${task.acceptance.map((x,i)=>`${i+1}. ${x}`).join('\n')}${task.recoveryCheckpoint?`\n\nПоследний сохранённый чекпоинт: ${task.recoveryCheckpoint.summary}\nСледующее действие: ${task.recoveryCheckpoint.next_action}`:''}`;assignment.focus();assignment.select();message('Задание выделено для ручного копирования. Автоотправка отключена.');});
    rest.append(view);
    if(run.status==='ACTIVE'&&s.phase==='WORKING'){
     const draft=document.createElement('button');draft.type='button';draft.className='secondary';
     draft.textContent='Заполнить черновик';
     draft.addEventListener('click',async()=>{
      draft.disabled=true;
      try{
       const r=await talk({type:'DRAFT_TASK',workerId:s.id,taskId:s.taskId,epoch:s.epoch});
       message(r.status==='DRAFT_FILLED_NOT_SENT'
        ?'Черновик задачи '+s.taskId+' заполнен во вкладке '+s.tabId+'. Ничего не отправлено.'
        :'Черновик не заполнен автоматически: '+r.status+'. Проверь вкладку.');
      }catch(e){message('Черновик не заполнен: '+e.message);}
      finally{draft.disabled=false;}
     });rest.append(draft);
    }
   }
   if(s.phase==='WORKING'){const ack=document.createElement('button');ack.type='button';ack.className='secondary';ack.textContent='Вижу прогресс';
    ack.addEventListener('click',async()=>{try{await talk({type:'ACK_PROGRESS',workerId:s.id,taskId:s.taskId,epoch:s.epoch});await refresh();message('Отмечено наблюдение владельца. Это не подтверждённый heartbeat агента.');}catch(e){message(`Ошибка наблюдения: ${e.message}`);}});rest.append(ack);}
  }else if(s.phase==='MISSING_TAB'){const repair=document.createElement('button');repair.type='button';repair.className='secondary';repair.textContent='Восстановить свободный слот';
    repair.addEventListener('click',async()=>{try{message('Проверяю старую вкладку…');await talk({type:'REPAIR_IDLE',workerId:s.id});await refresh();message('Свободный слот восстановлен. Сообщения не отправлялись.');}catch(e){message(`Ошибка: ${e.message}`);}});
    rest.append(repair);}
  el.append(badge,rest);slots.append(el);
 }
 if(selectedLease&&!run.slots.some(s=>s.id===selectedLease.workerId&&s.taskId===selectedLease.taskId&&s.epoch===selectedLease.epoch&&s.phase==='WORKING'))selectedLease=null;
 $('#approve').disabled=!selectedLease||run.status!=='ACTIVE';
 $('#apply-concurrency').disabled=!['ACTIVE','PAUSED_OWNER','PAUSED_AUTH','PAUSED_QUOTA'].includes(run.status);
 $('#draft-all').disabled=run.status!=='ACTIVE'||!run.slots.some(s=>s.phase==='WORKING'&&s.taskId);
 $('#export').disabled=false;$('#diagnostics').disabled=false;$('#timeline').disabled=false;$('#watchdog').disabled=run.status!=='ACTIVE';
 $('#start').disabled=run.status!=='STOPPED';
 $('#retry').disabled=run.status!=='ERROR_START';
 $('#pause').disabled=!['ACTIVE','STARTING'].includes(run.status);
 $('#resume').disabled=!['PAUSED_OWNER','PAUSED_AUTH','PAUSED_QUOTA'].includes(run.status);
 $('#stop').disabled=run.status==='STOPPED';
}
async function refresh(){try{const x=await talk({type:'STATUS'});render(x);message(`Режим: ${x.mode}. Черновики подготавливаются автоматически; Send отключён до подтверждения Web Companion.`);}catch(e){message(e.message);}}
async function action(type){try{message('Выполняется…');const result=await talk({type});await refresh();if(type==='STOP')message(`Остановлено. Закрыто подтверждённо своих вкладок: ${result.closed}`);}catch(e){message(`Ошибка: ${e.message}`);}}
$('#start').addEventListener('click',async()=>{try{const tasks=JSON.parse($('#plan').value);message('Открываю выбранное число вкладок и проверяю готовность ChatGPT (без Send)…');const result=await talk({type:'START',goal:$('#goal').value,tasks,maxConcurrent:Number($('#max-concurrent').value)});await refresh();message(`Запуск ${result.summary.id}: назначено ${result.assignments.length} задач. Отправка сообщений отключена.`);}catch(e){message(`Ошибка: ${e.message}`);}});
$('#draft-all').addEventListener('click',async()=>{
 const button=$('#draft-all');button.disabled=true;
 try{
  const r=await talk({type:'DRAFT_ALL'});
  await refresh();
  message('Подготовлено черновиков: '+r.filled+'/'+r.total+'. Отправлено сообщений: 0. '+(r.results||[]).map(x=>x.workerId+':'+x.status+(x.reason?'('+x.reason+')':'')+(x.diagnostic?'['+x.diagnostic+']':'')).join('; '));
 }catch(e){message('Не удалось заполнить черновики: '+e.message);}
 finally{button.disabled=false;}
});
for(const [btn,cmd] of [['#refresh','STATUS'],['#retry','RETRY_START'],['#pause','PAUSE'],['#resume','RESUME'],['#stop','STOP']])$('#'+btn.slice(1)).addEventListener('click',()=>cmd==='STATUS'?refresh():action(cmd));
$('#export').addEventListener('click',async()=>{
 try{const result=await talk({type:'EXPORT'});const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`hermes-autopilot-${result.run.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Снимок состояния экспортирован. Проверь содержимое перед передачей третьим лицам.');}
 catch(e){message(`Ошибка экспорта: ${e.message}`);}
});
refresh();

$('#preview').addEventListener('click',async()=>{
 try{const tasks=JSON.parse($('#plan').value);const result=await talk({type:'PREVIEW_PLAN',tasks,maxConcurrent:Number($('#max-concurrent').value)});
  message(`План: ${result.taskCount} задач, критический путь ${result.criticalPath.join(' → ')} (${result.criticalWork} усл. ед.), начальная параллельность ${result.initialParallelism}/${result.maxConcurrent}, конфликты ${result.contendedResources.length}. Вкладки не открывались.`);
 }catch(e){message(`Ошибка плана: ${e.message}`);}
});
$('#diagnostics').addEventListener('click',async()=>{
 try{const result=await talk({type:'DIAGNOSTICS'});
  diagnosticOutput.classList.remove('hidden');
  diagnosticOutput.textContent=result.warnings.length?`Предупреждения:\n${result.warnings.join('\n')}`:'Признаков локальных ошибок вкладок нет. Работа внешнего транспорта не проверена.';
  const blob=new Blob([JSON.stringify({...result,capturedAt:new Date().toISOString()},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`hermes-diagnostics-${result.run?.id??'empty'}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  message('Сформирована диагностика без текста задач и чекпоинтов.');
 }catch(e){message(`Ошибка диагностики: ${e.message}`);}
});

$('#apply-concurrency').addEventListener('click',async()=>{
 try{const limit=Number($('#max-concurrent').value);const result=await talk({type:'SET_CONCURRENCY',maxConcurrent:limit});
  await refresh();message(`Лимит установлен: ${result.summary.maxConcurrent}. Активные задачи не прерывались; новых назначений: ${result.assignments.length}.`);
 }catch(e){message(`Ошибка лимита: ${e.message}`);}
});
$('#approve').addEventListener('click',async()=>{
 if(!selectedLease){message('Сначала выбери назначение агента.');return;}
 const note=$('#approval-note').value.trim();
 if(!note){message('Нужна заметка о фактически проверенном результате.');return;}
 try{const result=await talk({type:'OWNER_APPROVE',...selectedLease,note});selectedLease=null;assignment.value='';$('#approval-note').value='';
  await refresh();message(`Владелец принял задачу. Следующих назначений: ${result.assignments.length}. Это не независимая аттестация.`);
 }catch(e){message(`Ошибка приёмки: ${e.message}`);}
});

// Read-only escape hatch when normal STATUS validation fails; does not modify stored state.
$('#rescue').addEventListener('click',async()=>{
 try{const result=await talk({type:'EXPORT_RAW'});if(!result.run){message('Аварийный экспорт: сохранённого запуска нет.');return;}
  const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;
  a.download=`hermes-rescue-${String(result.run.id??'corrupt').replace(/[^a-zA-Z0-9_-]/g,'_')}.json`;
  a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  message('Аварийный снимок сохранён без изменения состояния. Он может содержать текст задач; не передавай его посторонним.');
 }catch(e){message(`Ошибка аварийного экспорта: ${e.message}`);}
});
$('#timeline').addEventListener('click',async()=>{
 try{const data=await talk({type:'TIMELINE'});diagnosticOutput.classList.remove('hidden');
  diagnosticOutput.textContent=data.events.length?
   `События (последние ${data.events.length}):\n${data.events.map(e=>`#${e.revision} ${e.type} ${e.workerId??''} ${e.taskId??''}`).join('\n')}`:
   'Записей журнала пока нет.';
  message(`Событий учтено (в пределах журнала): ${Object.values(data.counts).reduce((a,b)=>a+b,0)}. Задержки и скорость не измеряются.`);
 }catch(e){message(`Ошибка журнала: ${e.message}`);}
});

$('#watchdog').addEventListener('click',async()=>{
 try{const result=await talk({type:'WATCHDOG_SCAN'});await refresh();
 message(result.alerts.length?`Watchdog: ${result.alerts.length} предупреждений; сообщений не отправлялось.`:'Watchdog: новых предупреждений нет. Достоверные события Companion пока не подключены.');}
 catch(e){message(`Ошибка watchdog: ${e.message}`);}
});

// Local-only preview; media bytes never leave the popup or enter chrome.storage.
import('./media_ui.mjs').then(module=>module.initMediaUi({talk,getSelectedLease:()=>selectedLease})).catch(()=>{
  const s=document.querySelector('#media-preview-status');if(s)s.textContent='Модуль локальной проверки вложений недоступен. Отправка отключена.';
});
