# SDD: Hermes/WebGPT — атомарная наблюдаемость и безопасное восстановление

**Редакция:** 0.1, 22 ноября 2026. **Статус:** архитектура + экспериментальный патч; НЕ производственный релиз и НЕ постоянно запущенный монитор.

**Область:** Hermes-WebGPT, Web Companion, локальный Relay, веб ChatGPT, Telegram, MCP/CDP, десятки параллельных инженерных агентов.
**Верифицированный базовый код:** Podtverzhdeno/Tunnel main, SHA 350ea33080bc35245aa6741f85fa926cc8a8fcaf.
**Важное расхождение:** Manifest в GitHub сообщает 0.6.6, но историческая документация описывает локально установленный Web Companion 0.9.7. Исходники установленной версии и живой процесс недоступны. Local Workspace MCP возвращал Session terminated, альтернативный канал 429. Не менять локальную инсталляцию на основании этого SDD.

## 1. Суть инженерной задачи

Система должна отвечать на четыре проверяемых вопроса: (1) кто и почему инициировал действие, (2) какие переходы состояния и реальные внешние эффекты подтвердились, (3) почему выполнение остановилось или перестало наблюдаться, (4) что разрешено делать дальше без второго Send, потери данных или обхода полномочий.

Нельзя объединять в один «успех» разные границы:

1. Авторизованный Telegram intent и исходный chat/topic.
2. Relay request/turn identity, транспорт, lease и commit статуса.
3. Browser tab/page ownership, один возможный клик и сохранённый user turn.
4. Подтверждённый ответ того же request_id, tool call и Hermes tool result.
5. **Реальный** outbound Telegram Send ACK в исходный чат/тему.
6. Состояние самого наблюдателя, которое может отличаться от состояния задачи.

Отсутствие события не означает failure; HTTP 200 не является доказательством ChatGPT Send; Relay done не равно Telegram delivered; DOM/LLM narration не являются независимой квитанцией результата.

## 2. Факты, подтверждённые чтением кода

- Hermes-WebGPT/relay.py: SQLite WAL с таблицами requests, transport_routes, turn_bindings, page_owners. Queue.claim() использует BEGIN IMMEDIATE и глобально допускает только **один** активный request в состоянии claimed/intent/saved. Следовательно, число вкладок расширения само по себе не даёт 25 одновременных model turns.
- Queue.event() проверяет lease и tab либо page_key, затем условно переводит claimed → intent → saved → done. После intent остановка или таймаут может быть uncertain. Это канонический слой для Relay, не браузерный DOM.
- web-companion/background_content.js: button.click() вызывается ДО stage(send_clicked). Следовательно, event send_clicked — не надёжная транзакционная запись намерения до внешнего эффекта.
- web-companion/background.js: telemetry к /v1/event имеет best-effort catch. Пропуск события возможен даже без падения браузера. chrome.storage.session хранит временные задания; chrome.storage.local содержит часть принадлежности и auth pause. Они не заменяют Relay.
- web-companion/bridge.py: журнал diagnostics/<job>.json сохраняет только последние 60 событий. recovery_candidate читает этот усечённый список для блокирующих причин. Исходный код также молча превращал испорченную историю в пустую. Новый патч этой ветки сохраняет раннюю опасную запись и отвергает повреждение.
- bridge.py completion_client_ready() сверяет heartbeat версии 0.6.6 и прямо отмечает, что heartbeat не доказывает профиль/аккаунт; сильная security boundary P085 не реализована.
- Hermes-WebGPT/monitoring/queue_monitor.py уже безопасно читает только id,state,reason,created из существующего Relay SQLite и приводит причины к allowlist. Не создавать второй конкурирующий queue owner.
- web-companion/chain2_lineage_monitor.py отслеживает parent→successor, request/handoff и report ACK. Не создавать второй Chain2 controller.
- docs/SEND_CONFIRMATION_FIX_20261111.md описывает более новую локальную 0.9.7 с проверкой bound answer и SHA ACK. Это историческое свидетельство локального релиза, а не доказательство текущей загруженной версии.
- Одновременно открыты PR #7/#8 (CDP), #10/#11 (пересекающиеся изменения Edge и background.js), #12 (исследование альтернативных transport), #13 (read-only triage). Нельзя объединять конкурирующие реализации без интеграционных тестов.

Не подтверждены: текущий Windows PID, точный browser worker SHA, живой SQLite, хотя бы один новый ChatGPT inference, настоящий Telegram delivery, причины реального пользовательского стопа.

## 3. Единая схема authority без второго планировщика

Telegram update → Hermes Gateway/tool permissions → Hermes-WebGPT Relay (request, turn, lease, transport) → один выбранный Browser Worker (Extension ИЛИ CDP) → ChatGPT web → верифицированный Hermes ответ и tool cycles → Gateway outbound Telegram Send → реальный Send ACK.

Независимый диагностический correlator читает подтверждённые события и формирует временную шкалу. Correlator имеет read-only права и не владеет очередью, не открывает вкладки, не вызывает инструменты, не выдаёт approval, не перезапускает неопределённый Send.

Каноническое владение:
- Relay: запрос, transport route, lease, side-effect intent, состояние.
- Браузер: вкладка/page identity и непривилегированные DOM наблюдения.
- Gateway: настоящая доставка Telegram.
- Chain2: parent-child handoff, report, SHA-bound ACK.
- Monitor: производный анализ и отметка пробелов.
- Рабочие агенты: выделенные Git-ветки и path ownership, но не полномочия обходить UI/owner approvals.

## 4. Формальные инварианты

| ID | Инвариант | Источник проверки |
|---|---|---|
| I01 | Один turn_id закреплён за одним transport после effect intent | turn_bindings / route |
| I02 | До Web Send требуется долговременный lease-bound intent ACK | target protocol, пока НЕ выполнено в старом content script |
| I03 | Click без достоверного receipt = UNCERTAIN, не RETRY | reconciler |
| I04 | Stale owner не может менять задачу после переизбрания | epoch/fencing; отдельный gate |
| I05 | Tab ID не доказывает владельца страницы | worker binding + canonical route |
| I06 | Старый опасный event не исчезает из recovery gate при усечении | sticky blocker в одном JSON |
| I07 | Повреждённую историю нельзя заменить пустой | 409 fail-closed |
| I08 | Report ACK сверяется с ID и SHA результата | существующий proof |
| I09 | Отсутствующий Telegram ACK не становится delivered | Gateway квитанция |
| I10 | Manual draft, auth change и UI challenge требуют остановки | browser/owner gate |
| I11 | Browser JSON не исполняет инструменты; Hermes проверяет schema/policy | tool policy |
| I12 | Heartbeat наблюдателя ≠ фактический прогресс агента | отдельные счётчики |
| I13 | Данные prompt, cookies, tokens и private URLs не входят в telemetry | allowlist |
| I14 | Agent self-review никогда не выдаёт host approval | реальные механизмы P085 |
| I15 | Отсутствие наблюдения = OBSERVATION_UNAVAILABLE, не HEALTHY | gap event |
| I16 | Нельзя повторять неизвестный Send при смене транспортного адаптера | noReplay |

Исправление этой ветки относится непосредственно к I06/I07. Нельзя выдавать его за полную реализацию I02/I04/I09.

## 5. Минимальный причинный контракт для будущих версий

Каждое событие включает:
schema_version, event_id, run_id, request_id_hash (HMAC, не сырой id),
turn_ref, agent_id, parent_step_ref, component, transport, process_boot_id,
effect_key, step_id, attempt_id, lease_epoch, per-run sequence,
observed_utc, process-local monotonic_offset_ms, event_type,
reason_code, evidence_class, evidence_digest, code_sha, next_action.

Все поля должны проходить allowlist + ограничение размера; исходные lease
secret, auth header, bearer, DOM, полные URL с query, response payload,
Telegram token и prompt хранить нельзя. Процессы различают timestamps
UTC и собственный monotonic clock: физический час не даёт причинного
порядка между машинами, поэтому требуются parent и sequence. Случайное
сортирование логов по секундам приводит к ложным RCA.

**Уровни доказательств:**
- E0: слова модели/человека о статусе;
- E1: UI/DOM и запись extension telemetry;
- E2: durable Relay transition и ACK с правильным lease;
- E3: независимая внешняя квитанция/подтверждённый read-back эффекта;
- E4: связанный ответ Hermes и реальный outbound Telegram ACK.
Копирование E0/E1 в красивую панель не повышает их доказательную силу.

## 6. Effect protocol и атомарность

Целевой порядок: QUEUED → CLAIMED → PREFLIGHT_OK → SEND_INTENT_COMMITTED
→ CLICK_ATTEMPTED → MESSAGE_SAVED → REPLY_VERIFIED → HERMES_ACCEPTED
→ TELEGRAM_DELIVERED. Ответвления: WAITING_APPROVAL, BLOCKED_PRE_SEND,
UNKNOWN_POST_CLICK, WAITING_EXTERNAL, CANCEL_REQUESTED, STOPPED,
OBSERVER_OFFLINE.

Транзакция SQLite **не может** включить commit ChatGPT. Поэтому нужное
свойство — не мифический exactly-once, а durable intent до клика,
at-most-one-effect-attempt-per-key, read-back при неизвестном результате,
явные полномочия и внешние квитанции. Перед тем как content script
нажмёт кнопку, он должен получить проверяемый ACK от Relay, который
уже связал request, lease, транспорт, page_ref, версию policy и срок.
Если ACK потерян, не нажимать. Если click outcome неизвестен, не нажимать
второй раз без независимого доказательства отсутствия первого эффекта.

Для CDP путь использует отдельный page UUID, нельзя считать его тем же
extension tabId. Для Gateway Telegram chat/thread и разрешение отправителя
обязательны отдельно. При ошибке разрешения owner approval не подделывать.

## 7. Наблюдаемость и журналирование

Старый v0.6.6 Bridge имеет 60-event tail. В текущей ветке:
- сохраняется формат массива c полями at,stage,reason и максимум 60 записей;
- удерживается САМОЕ РАННЕЕ записанное блокирующее событие в одной
  атомарной замене JSON, плюс последние 59 событий;
- плохой существующий JSON даёт 409 вместо потери прошлого;
- запись temp fsync → os.replace происходит до HTTP ACK;
- Send/claimed ownership/Telegram/worker не меняются.

Ограничения: пропущенный до записи event не восстановится; fsync файла не
равен гарантиям power-loss для каталога; 60 элементов не дают полного
поминутного replay. Полный строгий event history в будущем — приватный
append-only ledger с sequence, transactional outbox около Relay commit,
дедупликацией event_id, gap marker, data retention и независимой проверкой
секретов. Не использовать для этого вторую authoritative queue.

Нужно различать:
- last_worker_heartbeat: жив ли агент;
- last_observed_progress: появился ли новый подтверждённый факт;
- last_event_delivery: принимает ли монитор события;
- last_tool_activity: жив ли provider/сайт;
- external_effect_verified: есть ли независимый receipt.
Молчание браузера нельзя автоматически объявлять зависанием модели.

## 8. Причины остановки и проверяемые альтернативы

| Код | Непосредственный симптом | Что проверить | Безопасное действие |
|---|---|---|---|
| ADMISSION_HELD | новый claim запрещён | hold, campaign, owner | читать причины, не обходить |
| GATEWAY_DISCONNECTED | нет MCP/Bridge ответа | PID, порт, Tunnel статус отдельно | восстановить канал; не повторять Send |
| WORKER_NOT_READY | нет heartbeat/версия иная | loaded SHA, manifest, окна | остановить admission |
| CLAIM_ACK_LOST | lease создан, клиент не увидел | Relay DB lease/turn | сверить, не claim заново |
| UI_PRE_SEND_UNAVAILABLE | editor/button не найден | hydration, selectors, scope | только bounded preflight |
| AUTH_CHANGED | account/login challenge | owner confirmation | пользовательский вход |
| POST_SEND_UNKNOWN | есть click intent, нет receipt | saved user turn/read-back | reconcile, не replay |
| RESPONSE_NOT_BOUND | чужой/старый request_id | JSON + transcript order | reject |
| MODEL_STILL_GENERATING | Stop visible / partial | provider activity/timeout | ждать в пределах бюджета |
| TOOL_SCHEMA_INVALID | неизвестные arg/name | Hermes tool schema | не вызывать |
| APPROVAL_REQUIRED | нужен реальный владелец | approval broker | WAITING_APPROVAL |
| REPORT_ACK_MISSING | report без SHA receipt | ACK job/digest | не закрывать |
| TAB_IDENTITY_UNVERIFIED | route изменился | ownership & user-tab guard | сохранить вкладку |
| DB_LOG_CORRUPT | JSON не читается | источник/backup | 409, не перезаписывать |
| OBSERVER_OFFLINE | нет событий наблюдателя | процесс/сеть | observation gap |
| TELEGRAM_DELIVERY_UNKNOWN | Relay done, outbound ACK отсутствует | Gateway send event | отдельная проверка |
| CHAIN_CHILD_ORPHANED | successor не подтверждён | Chain2 controller | read-only reconciliation |

RCA отчёт должен хранить факты, competing hypotheses и тесты,
которые могли бы их опровергнуть, потом минимальное исправление с SHA
и отдельный независимый результат проверки. Нельзя выводить одну
причину только из старого request creation age.

## 9. Устройство для десятков агентов

Роли: A0 архитектура/SDD, A1 transport/identity, A2 браузер, A3 Relay,
A4 observability, A5 fault/chaos QA, A6 security/permissions, A7 release,
A8 независимое тестирование. Можно масштабировать роли клонами, но каждый
получает уникальный task_id, agent_id, owner, reviewer, branch, base_sha,
owned_paths, read_only_paths, lease/deadline, budget, exact acceptance tests
и checkpoint. Parent получает явно подтверждённый child artifact digest.

Правила:
1. Один файл изменяет один активный владелец; read-only анализ параллелен.
2. Любая запись в Git через SHA/CAS, конфликт — re-read/rebase/test.
3. Review на exact HEAD; один автор не может сам подписать собственный P0.
4. Merge queue проверяет интегрированный SHA, а не только изолированные PR.
5. Операционный request_id и effect_key переживают переподключение агента;
   attempt_id меняется. Тайм-аут агента не доказывает отказ внешнего эффекта.
6. Новый агент наследует durable журнал, а не unverifiable «готово» в чате.
7. Никаких прямых правок локальных приватных профилей, auth/token или
   мёржeй в main через диагностический агент.
8. Версия промпта — отдельный reviewable artifact с eval; саморевью
   модели не является ни доказательством, ни host approval.

Текущий конфликт PR: #10 и #11 меняют одни и те же background.js и popup;
нужен единственный интеграционный владелец. #13 пишет incident_triage.py,
поэтому не создавать конкурирующий triage.py. Эта ветка исправляет только
bridge.py, test_bridge.py и новые отдельные документы.

## 10. Численные приоритеты и честная неопределённость

Весовая оценка (не замеры): надёжность 35%, безопасность 30%,
доказуемость 25%, простота внедрения 10%.

| Альтернатива | R | S | O | P | Балл /5 |
|---|---:|---:|---:|---:|---:|
| UI retries + screenshots, без durable intent | 1 | 1 | 2 | 4 | 1.55 |
| Existing extension/Relay + read-only observer | 3 | 3 | 3 | 5 | 3.20 |
| Extension + pre-Send ACK + causal evidence | 4 | 5 | 4 | 3 | 4.20 |
| CDP-only transport без независимых квитанций | 2 | 3 | 3 | 2 | 2.55 |
| Официально поддерживаемый API + Hermes orchestration | 5 | 5 | 5 | 3 | 4.80 |

Последняя альтернатива может требовать отдельные scope, тарифы и
условия провайдера и НЕ является заменой запрошенного WebGPT UI без
решения пользователя. PR #12 — исследование, не подтверждение такого
доступа для локальной установки. Не обещать функциональность вне
предоставленных разрешений.

P0: защита noReplay, fail-closed лог, identity/auth boundary, честный статус.
P1: durable pre-Send handshake, отдельные доставки и causal event index.
P2: p95 phase latency, ресурсные бюджеты и справедливость очереди.
P3: LLM-предлагаемый self-healing — только за отдельным тестовым gate.

## 11. Матрица инъекции отказов

| ID | Неисправность | Ожидаемый результат |
|---|---|---|
| F01 | 75 событий после unknown | ранний blocker остаётся, recovery denied |
| F02 | 150 событий и более поздний blocker | первый blocker сохранён |
| F03 | повреждённый JSON на диске | 409, байты не потеряны |
| F04 | tab_identity_unverified после saved | recovery denied |
| F05 | unexpected_close, затем 65 событий | запрещён auto-recovery |
| F06 | crash после click до stage ACK | uncertain, no retry |
| F07 | MV3 service worker suspend/restart | resume без второго Send |
| F08 | tab ID reused / чужой route | чужая вкладка не закрыта |
| F09 | аккаунт переключён во время генерации | owner login, no silent action |
| F10 | пользовательский текст в composer | не стирать черновик |
| F11 | несколько Send buttons | ambiguity, без click |
| F12 | hydration delay до Send | bounded preflight only |
| F13 | /c/ без bound user turn | нет saved receipt |
| F14 | ассистент ещё генерирует | не считать final |
| F15 | неверный request_id | reject |
| F16 | неверная tool schema | no execution |
| F17 | host approval expired | no bypass |
| F18 | claim commit, HTTP ACK lost | lease reconciliation |
| F19 | два Relay процесса делают claim | один победитель |
| F20 | SQLite BUSY | bounded retry, no fabricated status |
| F21 | disk full during intent commit | browser click запрещён |
| F22 | process dies after intent | uncertain until read-back |
| F23 | event transport offline after click | diagnostic gap |
| F24 | report без SHA ACK | no accepted closure |
| F25 | ACK другого job | reject |
| F26 | valid report, чужая вкладка | не закрывать |
| F27 | Relay done, нет Telegram ACK | delivery unknown |
| F28 | Telegram 429/timeout | typed retry/uncertainty |
| F29 | не тот chat/thread | не заявлять доставку |
| F30 | Edge parent exits, child survives | separate process owner diagnosis |
| F31 | нет worker heartbeat | наблюдение неизвестно |
| F32 | 30 агентов меняют background.js | ownership/CAS conflict |
| F33 | 30 агентов один effect_key | один claim |
| F34 | stale epoch пытается писать | fence rejects |
| F35 | лог содержит token/prompt | redaction test fails |
| F36 | event sequence lost | explicit HISTORY_GAP |
| F37 | telemetry exporter 503 | buffer and alert |
| F38 | monitor killed, Hermes continues | observer-offline, not job fail |
| F39 | source 0.6.6 vs loaded 0.9.7 | stop rollout |
| F40 | CDP пытается захватить extension turn | turn_bindings reject |

Для каждого test case: baseline SHA, fault seed, environment, actual event
sequence, expected state, exit code, security/privacy check, reviewer,
rollback and residual risk. НЕ ставить PASS без реального запуска.

## 12. Metrics и измерения

Отдельные интервалы: enqueued→claimed, claimed→content_ready,
preSend→intent_ack, intent→click, click→message_saved,
saved→answer_verified, reply→Hermes_tool/next_turn,
Hermes_final→Telegram_Send_ACK. Для p95 нужна метрика по каждой фазе,
а не возраст с момента создания запроса.

Низкокардинальные показатели: relay_state{state,transport},
uncertain_effect{reason_class}, diagnostic_corruption_total,
sticky_hazard_retained_total, event_ack_failure_total,
observer_gap_seconds, tab_ownership_conflict_total,
telegram_verified_delivery_total. Запрещены request IDs, prompt и URL
как labels metrics.

Load gates: 1/2 sessions для точного noReplay, 6 tabs/2 claims как
консервативный Edge режим из PR #11, 25 tabs/4 claims только после
реального CPU/память/p95; 30+ разработчиков агентов работают
параллельно в разных branches, но нынешний Relay имеет
**один активный inference request**. Большое число инженеров не означает
безопасных 30 одновременных кликов ChatGPT в одном профиле.

## 13. Приёмка и rollout

| Gate | Что требуется | Сейчас |
|---|---|---|
| G0 | exact installed version, PID, SHA, ownership, permissions | BLOCKED: disconnected workspace |
| G1 | полный test_bridge + новый regression, syntax | добавлены тесты, полный прогон не подтверждён |
| G2 | browser+bridge+Relay safe fixture, noReplay | не выполнен на этой ветке |
| G3 | crash/restart before/after Send и receipt readback | не выполнен |
| G4 | independent origin/credential/approval security review | не выполнен |
| G5 | Windows Edge CPU/p95 6/25 tabs | не выполнен |
| G6 | real ChatGPT response + Telegram same-topic ACK | не выполнен |

Порядок: M0 установить фактическую версию; M1 принять изолированный
safety fix после G1; M2 разработать версионированный pre-click ACK;
M3 добавить производный redacted event index; M4 провести staged
canary и rollback; M5 допустить советующий автоанализ и только
доказуемо безопасные автоисправления.

Откат требует backup и сравнения SHA установленного worker/source,
проверки idle окна, сохранения уже начатых jobs и noReplay,
а затем контролируемого перезапуска. Git revert сам по себе
не откатывает загруженный service worker. Не очищать uncertain events.

## 14. Ссылки

Внутренние: Hermes-WebGPT/relay.py,
Hermes-WebGPT/monitoring/queue_monitor.py,
chat-launch-smoke-desktop-test/web-companion/bridge.py,
background.js, background_content.js, chain2_lineage_monitor.py,
Hermes-WebGPT/AGENTS.md, docs/HERMES_MONITORING_RU.md,
Hermes-WebGPT/docs/SEND_CONFIRMATION_FIX_20261111.md.

Стандарты и первичные источники (дизайн, НЕ доказательство установки):
- SQLite isolation / WAL: https://sqlite.org/isolation.html и https://sqlite.org/wal.html
- W3C Trace Context: https://www.w3.org/TR/trace-context/
- OpenTelemetry GenAI: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Chrome MV3 lifecycle: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle
- Chrome native messaging: https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- GitHub branch protection: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- Temporal retry reasoning: https://docs.temporal.io/encyclopedia/retry-policies

Различать состояние исходников, синтетический тест, локальный процесс,
живую модель и фактический Telegram delivery на каждом отчёте.
