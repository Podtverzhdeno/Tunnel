# Hermes-WebGPT — SDD: атомарная наблюдаемость, остановки, интеграция десятков агентов

**Decision:** `SDD-HWGPT-REVIEW-2026-11-22-C84E`. **Статус:** `PROPOSED / SOURCE-REVIEWED / NOT DEPLOYED`.

**Основание:** `Podtverzhdeno/Tunnel@350ea33080bc35245aa6741f85fa926cc8a8fcaf`, отдельный старый форк ядра `Podtverzhdeno/hermes-agent@7e9100901819ee44c16b4ddcb79a6bcb7909f591`. Этот документ — **интеграционное дополнение** к [PR #19](https://github.com/Podtverzhdeno/Tunnel/pull/19) и исходной [SDD](https://github.com/Podtverzhdeno/Tunnel/blob/docs/hermes-gpt-atomic-sdd-monitor-review-20261122/Hermes-WebGPT/docs/HERMES_GPT_ATOMIC_INTEGRATION_SDD_20261122.md), а не новый scheduler, монитор, авторитетный ledger, отправитель ChatGPT или интерфейс управления браузером.

## 1. Реальный контур, без подмены сторонними «Hermes GPT» проектами

Telegram → Hermes Gateway → `Hermes-WebGPT/relay.py` (SQLite WAL, `requests`, `turn_bindings`, `transport_routes`, `page_owners`) → выбранный `extension` либо `playwright` transport → Web Companion или CDP → ChatGPT Web → bound response → Hermes → отдельная Telegram отправка и независимый ACK.

В main `Queue.claim` использует `BEGIN IMMEDIATE` и глобальный запрет второго активного `claimed/intent/saved`. Следовательно, **50 HTTP ожидателей и десятки reviewing-агентов НЕ равны 50 параллельным browser Send**. Без пер-ресурсного lease/capacity/fence нельзя повышать число одновременно мутирующих вкладок.

В GitHub `chat-launch-smoke-desktop-test/web-companion/manifest.json` — версия **0.6.6**; в ранее зафиксированном [SEND_CONFIRMATION_FIX_20261111](./SEND_CONFIRMATION_FIX_20261111.md) описана установленная Windows сборка **0.9.7**. Ранее в рабочих тестах отмечен backend `web-chatgpt-high-cdp`, но это **историческое** свидетельство, а не новая проверка рабочего CDP. Local_WorkSpace сейчас завершился `Session terminated`. Поэтому активные PID/порт, версия загруженного service worker, реальный профиль, байты установленного расширения, Telegram provider ACK — **UNKNOWN**. Слепое копирование публичного 0.6.6 поверх рабочего 0.9.7 запрещено.

Соседние проекты `asimons81/hermes-gpt` (ChatGPT MCP → Hermes), `intellectronica/hermes-chatgpt-extension` (Codex desktop → Hermes), `Dworr21/chatgpt-bridge` (Hermes → Web ChatGPT) и `abundantbeing/hermes-browser-extension` (side panel → Hermes) — **сравнительные источники, не установленный здесь продукт**. У них различаются направление вызова, версия и модель разрешений.

## 2. Конкретные факты, риски и отдельная сила доказательств

| ID | Источник (точный Git blob) | Подтверждено | Не доказано |
|---|---|---|---|
| H01 | `relay.py` main `9c30cbe864556fbf497ed2dea6619c58fd7156ff` | Глобально один активный request; `turn_id`, `lease`, транспорт фиксируются в SQLite | 5/32/64 одновременно работающих UI отправителей |
| H02 | `web-companion/background_content.js` main `a611a8cbc771658aa5209d991e647e3dadfb7f29` | `button.click()` выполняется **до** best-effort `stage("send_clicked")`; loss of telemetry после клика возможен | Отсутствие `send_clicked` означает «не отправлено» |
| H03 | `web-companion/bridge.py` main `eaf301ac15ad301464159438d6431cbc91d3e323` | Кольцо из 60 последних событий теряет старые blockers; PR #14 предлагает sticky earliest blocker + fail-closed malformed JSON | Патч уже установлен в работающий профиль |
| H04 | `monitoring/queue_monitor.py` main `c22e457ec2e37f3a6fa9b8c411c7185c9bcddecd` | Read-only `id,state,reason,created`; age = с момента создания, не текущая latency стадии | Нет тревог = рабочая модель и доставленный Telegram ответ |
| H05 | PR #13 `incident_sampler.py` blob `5dedc7a816a8122ae9305571f654d84a047568eb` | Отдельный приватный SQLite `BEGIN IMMEDIATE` атомарно сохраняет baseline + incident | Фоновый Windows монитор уже запущен |
| H06 | PR #19 `queue_monitor.py` blob `d059632143481837428d769d838a2bd94329c09b` | `illegal_state_regression` для terminal→active и отката phase | Точная причина отката, разрешение autorepair |
| H07 | PR #15 `relay.py` blob `7b4afaa3af09b9e18cc832db60f22ffc4c591d54` | Bounded 64 HTTP waiters / 8 per turn, SQL journal transitions, startup reconciliation; **воспроизведён false-complete history bug** | Текущий PR готов к merge, исторический event prefix полон |
| H08 | PR #16 mirror guard | Exact SHA совпадение 7 публичных файлов и ограничение source permissions | Точное совпадение с установленным и запущенным 0.9.7 |

**Слои доказательства:** `SOURCE_BLOB`, `OFFLINE_UNIT`, `CI_EXACT_MERGED_HEAD`, `INSTALLED_BYTES`, `LOADED_RUNTIME`, `AUTHENTICATED_BROWSER_RECEIPT`, `TELEGRAM_PROVIDER_ACK`. Любой нижний уровень **не повышает** автоматически уверенность до следующего. Записи агента или модели — только self-report.

## 3. Воспроизведённый P0: SQL журнал ложно называет старую историю полной

В PR #15 SQL trigger `AFTER INSERT` и `AFTER UPDATE OF state` пишет события только **после** своей установки. Однако `state_event_tail()` выводит `history_complete_for_current_requests=true`, если у каждой строки есть **хоть одна** запись в `request_state_events`.

**Прямой in-memory SQLite контрпример:** существующая до миграции строка `requests(id='old',state='claimed')` → установка новой таблицы и trigger → `UPDATE state='uncertain'` → появляется **один** event `claimed→uncertain` → `history_missing_request_count=0`, `history_complete_for_current_requests=true`, хотя события создания и `queued→claimed` отсутствуют. Это воспроизведённая ошибка **метаданных о полноте доказательств**, не инцидент браузера. Регрессия передана [автору PR #15](https://github.com/Podtverzhdeno/Tunnel/pull/15#issuecomment-7078301648).

**Исправление:** добавить версионированный `journal_capture_epoch`; доустановочные строки пометить `history_prefix_unknown=true` навсегда либо до **независимого** подтверждения. Только фактически записанный `INSERT` под гарантированным trigger + непрерывный дальнейший журнал позволяет `complete=true`. Не создавать фиктивные старые события; не применять TTL/удаление к unresolved effects. Тесты: старый активный request + один update ⇒ INCOMPLETE; свежий insert+updates ⇒ возможна COMPLETE; rollback ⇒ audit event не сохраняется; dropped trigger / SQLITE_FULL ⇒ UNKNOWN/блокировка, а не green.

**Потенциальный риск доступности:** журнал `request_state_events` пока без проверенного retention/size budget и может расти неограниченно. SQL trigger атомарен с записью запроса; переполнение БД блокирует и переход состояния. Нужны disk/WAL metrics, fs-full chaos, gap-aware архив (никогда не удалять неизвестный исход без квитанции), регрессии restart. Это исходниковый риск, **не** зафиксированное переполнение диска. [Отдельный комментарий PR #15](https://github.com/Podtverzhdeno/Tunnel/pull/15#issuecomment-7078314601).

## 4. Решение: одна составная цепочка вместо множества независимых агентов-мониторов

**Producer / authority:** только Relay `requests` + исправленный event ledger PR #15. Browser reports — телеметрия, не факт исполнения эффекта. One writer per browser lane.

**Pre-send policy:** записать durable intent/lease и получить ACK **до** единственного `button.click()`, с привязкой к `request_id, turn_id, attempt_id, principal_ref, tab_id, document_epoch, browser_lane_id, code_sha`. Переход `intent→click_attempted` не эквивалентен подтверждённому внешнему Send. Потерянный ACK после возможного клика = `CLICK_OUTCOME_UNKNOWN` и **noReplay**, никакого fallback между extension и CDP.

**Browser / recovery:** использовать sticky event evidence PR #14; source mirror gate PR #16; отдельный фактический loaded-worker provenance и подтверждённый владелец вкладки/аккаунта. Разные агенты не должны касаться одной живой вкладки без CAS fence.

**Projection:** использовать существующий `queue_monitor.py` с PR #19 rollback alerts и **один** PR #13 atomic sampler. Не запускать одновременно два процесса с независимым baseline-file / sampler, не превращать storage telemetry в scheduler, не разрешать автоматический Send через observer.

**Completion truth:** раздельно `request accepted`, `claimed`, `intent`, `click possible`, `user turn saved`, `assistant report bound`, `Relay.done`, `Telegram provider ACK`. Состояние `Relay.done` **не** обещает доставку Telegram. Ответ модели и `worker.stopped` не доказывают успешный внешний бизнес-эффект.

**Trace payload (redacted):** `trace_id, project_build_sha, request_digest, turn_digest, attempt_id, source_instance, source_seq, observed_at, evidence_status, state, reason_code, last_durable_state, unknown_effects, safe_next_action, bridge_profile_epoch`. В публичной диаграмме — псевдонимизированные идентификаторы; никаких prompts, bearer, CDP URLs, cookies, приватных chat/topic ID. Последовательность по источнику, не универсальные wall-clock timestamps.

## 5. Причины остановки: наблюдение ≠ гипотеза ≠ действие

| Code | Основание | Безопасная трактовка |
|---|---|---|
| `OBSERVATION_UNAVAILABLE` | Local MCP/SQLite/JSON недоступны | Не объявлять модель упавшей; не мутировать очередь |
| `QUEUED_BEHIND_ACTIVE` | `queued>0`, `claimed/intent/saved>0` | Глобальный однопоточный Relay, не «модель зависла» |
| `ADMISSION_BACKPRESSURE` | HTTP 429/Retry-After | Waiter budget, ограниченный клиентский backoff; не плодить очереди |
| `CLAIM_ACK_UNKNOWN` | Claim committed, ответ потерян | Сверить lease и request; не передавать другому воркеру вслепую |
| `CLICK_OUTCOME_UNKNOWN` | Durable intent/клик возможен, нет квитанции | Запретить повтор; независимый read-back |
| `RECOVERY_EVIDENCE_CONFLICT` | Sticky unknown + новый eligibility | Инцидент, не автоматическое восстановление |
| `HISTORY_PREFIX_UNKNOWN` | Старый request после миграции SQL журнала | Не выводить `history_complete` |
| `ILLEGAL_STATE_REGRESSION` | Состояние той же записи движется назад | Обнаружить, но не «починить» без выяснения писателя |
| `AUTH_CHANGED` | Профиль/owner/document изменился | Пауза без автоматической смены аккаунта |
| `MV3_OR_TAB_LOST` | Content script/tab epoch исчез | Read-only reattach и UNKNOWN до доказательств |
| `MODEL_RESPONSE_UNBOUND` | Неверный `request_id`, JSON или tool schema | Reject, никаких исполняемых инструментов |
| `TELEGRAM_DELIVERY_UNKNOWN` | Нет фактического Bot API receipt | Отдельная сверка Telegram, не повтор модели |
| `DISK_OR_WAL_PRESSURE` | Audit SQL write fails/SQLite busy/full | Сигнал ресурса, сохранять внешние noReplay ограждения |
| `CI_OR_LOADED_SHA_UNKNOWN` | Pending/cancelled/skipped CI или не тот загруженный билд | Release HOLD; provenance/rollback |

Каждая строка имеет `evidence_status=observed|inferred|unknown`, проверенные источники, конкурирующие гипотезы и falsifying test. Зелёный код монитора означает *наблюдение выполнено*, не *все внешние эффекты доказаны*.

## 6. Десятки агентов — один authority и строгий handoff

На момент обзора открыто **17** PR в Tunnel и до **30** в hermes-agent; это snapshot, не полное будущее число. Назначить эксклюзивных владельцев участков: Relay ledger и waiter PR #15, Bridge и sticky блокеры PR #14 (с учётом интегрированного PR #5), browser tab/Edge owner PR #10/#11 по одному merge-кандидату и проверке зеркал #16, queue monitor PR #19, sampler PR #13, независимый chaos QA, release integrator. PR #7/#8 конкурируют за CDP. Условие merge: exact parent/head SHA + совместная ветка, зеленые реальные CI tests на **итоговом** SHA, не раздельно на отдельных PR.

**При потере агента**: сохранить `task_id, owner, exact_base_sha, last_head_sha, owned_paths, latest_authoritative_state, unknown_effects, last_verified_receipt, tests_executed, next_safe_action`. Агент не считается прекратившим внешнее действие по факту прекращения ответов в чате. Следующий агент начинает с read-only reconciliation, не повторяет Send.

Программы и журналы наблюдения не требуют непрерывного интерактивного чата. Но пока **нет реально запущенного мониторинга** на Windows, документация и 32 локальных теста соседнего PR не означают автоматическое наблюдение между обращениями.

## 7. Релизная последовательность и строгие отрицательные сценарии

| Gate | Критерий | Status |
|---|---|---|
| G0 | Exact source vs installed 0.9.7 vs loaded runtime SHA; owner-approved isolated canary | BLOCKED/UNKNOWN |
| G1 | Исправить false-complete prefix + SQL-full/corrupt/restart tests PR #15 | OPEN P0 |
| G2 | PR #14 сохраняет recovery blocker после 75/150 benign events; malformed logs return 409 | SOURCE CANDIDATE |
| G3 | PR #13 atomic baseline+incident + PR #19 rollback + один correlator; full test discovery / CI SHA | NOT INTEGRATED |
| G4 | Preclick durable intent ACK, exactly one click, noReplay across service-worker death | NOT INSTALLED |
| G5 | 1/8/32/64 waiter tests, per-turn budget, no orphan/duplicate claim | CANDIDATE #15 |
| G6 | Real signed-in ChatGPT bound response + actual Telegram provider ACK, rollback under owner approval | NOT VERIFIED |

Негативные тесты минимум: crash before/after intent, crash after click before telemetry, stale worker `document_epoch`, two actors one tab, mixed extension/CDP, 64 waiters on one turn, response delivered after timeout, historic row + one new SQL event, full disk, wrong request ID, account switch, 70+ benign recovery events, two competing baseline writers, handler shutdown, no Telegram receipt, and overwritten browser mirrors. Safety invariants: **0 confirmed duplicate Send; 0 false verified-success; 0 unauthorized browser writes; 0 blind retry of UNKNOWN; every staged failure has typed reason + source or UNKNOWN**. Latency p95/p99 cannot be set without actual measurements.

Согласно [`Hermes-WebGPT/AGENTS.md`](../AGENTS.md), standing consent для проектной разработки **не подменяет** реальное foreground owner approval из локального specification broker. Не подделывать его и не обходить P085, не перезапускать живой профиль/Relay, не переносить приватные данные в GitHub.

## 8. Evidence log / handoff (append-only)

- **2026-11-22 SOURCE:** обнаружены source версии 0.6.6 и историческая installed 0.9.7. Loaded bytes, live Windows and Telegram delivery не подтверждены.
- **2026-11-22 SOURCE:** одна глобальная Relay claim; click перед `send_clicked`, event ring ограничен 60. Объединение требует независимого предкликового durable ACK и noReplay.
- **2026-11-22 REPRODUCED in-memory SQLite:** legacy active row → единственный новый trigger update → ложное `history_complete=true`. Комментарий [PR #15](https://github.com/Podtverzhdeno/Tunnel/pull/15#issuecomment-7078301648).
- **2026-11-22 SOURCE RISK:** unbounded SQL journal/no explicit disk capacity gate; комментарий [PR #15](https://github.com/Podtverzhdeno/Tunnel/pull/15#issuecomment-7078314601).
- **2026-11-22 SOURCE REVIEW:** PR #13/14/15/16/19 содержат дополняющие, ещё не интегрированные изменения. Нельзя запускать второй монитор или чередовать источники истины.
- **2026-11-22 ISOLATED CODE:** [hermes-agent PR #24](https://github.com/Podtverzhdeno/hermes-agent/pull/24) содержит read-only cross-PR checker и **32/32 локальных изолированных теста**; этот CI/код не подтверждает Windows installation.
- **2026-11-22 RUNTIME:** Local_WorkSpace `Session terminated`; реальный браузер/Gateway/Telegram не тронуты.

**Продолжение для любого из десятков агентов:** это задача высокой сложности с обязательной проверкой доказательств, воспроизводимым failing-before/passing-after, отрицательными тестами и пересмотром выводов при перемещении branch HEAD. Никогда не объявлять работу «10/10/DONE» по одному успешному тесту и не считать self-report подтверждением результата. Должны быть известны статус реально установленного кода, последняя неизменяемая квитанция, оставшиеся unknown effects, точный блокер, ответственное лицо и безопасный следующий шаг. Этот SDD хранит инструкции между агентами, но **не является ни памятью модели, ни запущенным фоновым сервисом**.
