# Hermes Temporary Chat v2 — протокол, углы отказа и план миграции

**Revision:** 2026-11-22 • **Status:** DESIGN ONLY / NO-GO • **Sources:** `Hermes-WebGPT/relay.py` v1, `Hermes-WebGPT/patches/manual-draft-20261110/hermes_relay_bg.js`, `.../send-confirmation-20261111/hermes_relay_content.js`, `Hermes-WebGPT/temporary_chat_ledger.py`.

Эта спецификация нужна, чтобы не превратить однострочную замену URL в потерю/дублирование запросов и не принять обычный сохранённый разговор за Temporary. Нельзя применять к действующему Windows-расширению без проверки точного SHA загруженных файлов и изолированного прогона.

## 1. Идентичности и источники истины

| Идентификатор | Владелец | Назначение | Не должен заменяться |
|---|---|---|---|
| `request_id` (UUID) | Relay canonical DB | Одна логическая отправка, идемпотентность | Заголовком страницы, текстом |
| `lease` (unguessable) | Relay | Одна активная доставка job | `tab_id`, `window_id` |
| `session_id` (UUID) | Relay/ledger | Одна временная беседа в живой вкладке | `/c/...` или названием вкладки |
| `profile_nonce` | Browser process owner | Отдельная изоляция Edge | Локальным путём, user email |
| `tab_id` + `window_id` | MV3/browser | Привязка внутри profile | Только URL |
| `document_epoch` | Trusted adapter from extension/documentId | Навигационный epoch и защита от SPA/пересоздания документа | Старым `tab_id` |
| `ui_proof_nonce` | Trusted UI adapter | Одноразовое наблюдение Temporary UI | Просто query parameter |
| `evidence_nonce` | Trusted content adapter | Одно authored user turn за request | Незавершённым вводом/скриншотом |
| `response_receipt` | Relay-bound adapter | Подтверждённый request-bound assistant output | Первой ответной bubble, любым JSON |

UI proof обязательно связан с текущим профилем, текущей вкладкой и `document_epoch`; TTL не заменяет проверку актуального состояния UI непосредственно перед Send.

**ЕДИНСТВЕННЫЙ источник правды о Send:** транзакционное состояние на стороне Relay. Нынешний отдельный `TemporaryChatLedger` — работающий прототип, а не второй независимый журнал при живом деплое. При интеграции либо адаптировать его таблицы к существующему `Relay Queue` в **одной** SQLite-транзакции, либо полностью переиспользовать канонические Relay intent/lease таблицы. Две БД с separate `ARMED` чреваты split-brain и запрещены.

## 2. Versioned API shape (предлагается, ещё не существует)

```text
POST /bridge/v2/claim
  worker_id, protocol=temporary-v2
  returns request_id, lease, session_id, profile_nonce_alias
  no default v1-to-v2 fallback

POST /bridge/v2/bind-tab
  request_id, lease, session_id, window_id, tab_id, document_epoch
  returns {state: "BOUND"} ONLY on verified ownership

POST /bridge/v2/verify-temporary
  request_id, lease, session_id, tab_id, document_epoch,
  proof_nonce, trusted_adapter_verdict
  returns {state: "TEMP_VERIFIED", expires_at}
  DO NOT infer verdict from URL; absence => no Send

POST /bridge/v2/send-intent
  request_id, lease, session_id, tab_id, document_epoch, proof_nonce
  atomic commit ARMED + exactly one grant
  no GET or POST may grant second click after ambiguous ACK

POST /bridge/v2/user-observed
  request_id, lease, tab_id, document_epoch, evidence_nonce
  only EXACT authored user turn, after Send intent

POST /bridge/v2/response-observed
  request_id, lease, tab_id, document_epoch, response_receipt,
  payload -- only if final answer is request_id-bound and
  unambiguously occurs after that user turn (authenticated)
  semantics: idempotent ACK of same receipt, conflicting receipt -> UNCERTAIN

POST /bridge/v2/delivery-ack
  request_id, lease, delivery_id
  independent of response acknowledgment

POST /bridge/v2/release-tab
  session_id, lease, owner-initiated release policy
  only when safe terminal+ACK and no active/unknown Send
```

**Безопасность API:** строго `127.0.0.1`, bearer key не в адресной строке, проверка Host, отсутствие endpoint для страницы (только extension/MCP principal), схема отказов с кодами без prompt, CSP/host permissions minimal, replay lock. Этим описанием НЕ утверждается наличие этих endpoint в текущем коде. Не реализовывать «обратную совместимость» как разрешение старому `/bridge/result` привязать ответ к Temporary без URL.

## 3. State machine и правила повторов

```text
NEW --browser attached--> BOUND
BOUND --trusted UI proof--> TEMP_VERIFIED
TEMP_VERIFIED --draft prepared--> PRE_SEND
PRE_SEND --Relay durable INTENT--> ARMED
ARMED --exactly 1 browser click attempted--> SENT_UNKNOWN
SENT_UNKNOWN --exact authored user turn--> USER_CONFIRMED
USER_CONFIRMED --final request-bound assistant--> RESPONSE_CONFIRMED
RESPONSE_CONFIRMED --Relay durable ACK--> RESULT_ACKED
RESULT_ACKED --optional delivery ACK--> DELIVERED
DELIVERED --owner release policy--> TAB_ARCHIVED

Any state --tab/account/doc/temp mode lost--> PAUSED_RECONCILE
Post-ARMED unexpected outcome -> UNCERTAIN_NO_REPLAY.
Every new turn within same living session requires new UI proof and request ID.
```

Relay must record `ARMED` **before** handing any permission to click Send, regardless of whether a network ACK is received. If ACK is lost, the client can query the original state but cannot arm again. `noReplay` means at-most-once effect, not exactly-once user-visible delivery.

### Fault boundaries

| Момент сбоя | Допустимое действие | Запрещённое действие |
|---|---|---|
| До claim | Оставить request queued | Создать «фантомный» grant |
| Claim прошёл, ACK потерян | Проверить authoritative lease; pause при несоответствии | Claim заново без lease resolution |
| Tab создан, запись tab binding не сохранилась | Отметить orphan, сохранить вкладку | Создать новую вкладку + продолжить без проверки |
| Temporary UI не подтвердился | Вернуть typed fail `temporary_ui_unverified` | Send в обычный чат |
| PRE_SEND, кнопка отсутствует | Pre-intent safe editor reconciliation only | Самовольное создание нового request |
| ARMED, браузер не успел click | UNCERTAIN_NO_REPLAY (возможна потеря одного запроса) | Повторный click после reboot |
| После click, до видимого user turn | UNCERTAIN_NO_REPLAY + ручная сверка | Считать неотправленным и переотправить |
| User turn подтвердился, генерация идёт | Сохранить вкладку, ждать bounded | Закрыть через 5 секунд как будто done |
| Ответ виден, но request_id другой | UNCERTAIN / reject | Приписать ответ текущему запросу |
| Ответ подтвердился, Relay ACK потерян | Повторить receipt confirmation с тем же id, если backend state позволяет | Повторный Send/новая беседа |
| Relay done, Telegram ACK потерян | Идемпотентный delivery receipt по отдельному ID | Повторная отправка модели |
| Browser/CDP client исчез | Detach; session pause; retention evidence | Kill Edge/безусловный restart |
| Browser crash/OOM | Diagnostics/uncertain; ручная сверка | Придумать восстановление Temporary из истории |
| Windows перезапустился | Reload только безопасный broker; `ARCHIVED` или `PAUSED` | Удалить noReplay fence |

## 4. React/DOM, Temporary UI, hot-path CPU

Для настоящего `hermes_relay_content.js` v0.9.7 тестировать **отдельно**:

1. `documentId` должен совпадать для Send authorization и assistant evidence.
2. Видимый Temporary Chat indicator должен иметь внятный семантический проверяющий адаптер, устойчивый к локализации; если исчез, не угадывать.
3. Состояния `Generating`, `Stop generating`, Tool call, auth dialog, retry, storage expired, site CSP errors должны быть различимы; `aria-label` зависит от языка.
4. MutationObserver должен делать дешёвую отметку «dirty», а не запускать тяжёлый querySelectorAll на каждую букву streaming. Прототип `dom_reconcile_budget.cjs`: minGap=1200ms, heartbeat=5000ms; fallback покрывает пропущенные события.
5. Актуальный редактор и Send button искать непосредственно перед click. Кешировать допустимо shadow-root discovery на короткий TTL; никогда не кешировать разрешение Send.
6. После saved-stage ACK можно прекратить частые поиски текста, но **продолжать** дешёвый account/temp/document health guard. При потере health guard никакой новый Send.

## 5. Процесс и окно — разные сущности

Отслеживать `msedge.exe` root PID и `tabId/windowId` по отдельным журналам:

- `tab_removed`: событие расширения, tab принадлежит задаче или не принадлежит;
- `window_removed`: исчезает GUI окно, может быть закрыта последняя вкладка;
- `process_exit`: завершение самого owner-launched Edge, причина нужна из Windows WER/Event Viewer/родительского Job Object;
- `cdp_detached`: контроллер ушёл, браузер остаётся;
- `renderer_crashed`: страница недоступна, процесс браузера может существовать.

**Recovery policy:** owner-driven Edge instance не создавать в краткоживущем Python Playwright context, который закрывается в `finally`. Для Playwright использовать только attach к существующему процессу, а не `launch_persistent_context` с `context.close`. В ветке PR #8 текущий `browser.close()` после `connect_over_cdp` предназначен как detach, но тест на данной системе отсутствует.

Если last-tab closure приводит к исчезновению окна, нужен UI anchor или явная политика «сохранять последнюю системную вкладку». Anchor предпочтительнее лёгкий `about:blank`, не ещё одна тяжёлая ChatGPT-страница. Не внедрять автоматически, пока не подтверждена H1; это может мешать работе пользователя.

## 6. Admission control, fairness, CPU budget

Начальные лимиты **гипотеза, а не измеренный оптимум**:
- `max_generating_per_profile=1`;
- `max_tabs_in_pre_send=1`;
- `max_unacknowledged_result=1`;
- pending queue в Relay (без браузерных вкладок) ограничена политикой уже существующей очереди;
- пока нет UI proof и session lease v2, **не принимать** Temporary auto-send job.

При обнаружении `RESOURCE_EXHAUSTED` не выполнять слепой перезапуск Edge: ограничить admission; сохранить noReplay state. Критерий fairness: ранее подтверждённые requests не голодают из-за новых, но UNKNOWN всегда блокирует небезопасный reuse.

Измерять `cpu_equivalent_cores`, p50/p95 CPU% машины, процессный churn, вкладки в живой генерации, Edge Task Manager renderer vs extension, p95 end-to-end latency, число duplicates/uncertain. Сравнивать только при одинаковых запросах, аккаунте и режиме мощности; p95 из пяти срезов исследовательский.

## 7. Миграция (каждый шаг является release gate)

**M0 — provenance:** прочитать установленную версию Web Companion на Windows с SHA каждого файла, реальный `manifest.json`, изолированный профиль и список разрешений; зафиксировать backup + способ мгновенного отката. Не копировать snapshot 2026-11-10 поверх текущего 2026-11-22.

**M1 — read-only audit:** протестировать старые JS/Python tests на точном установленном source. Исправления v0.6.6 smoke не считать покрытием Hermes 0.9.7.

**M2 — canonical DB:** расширить одну и ту же Relay SQLite schema по versioned transport, не создавать конкурирующую независимую базу ARMED; миграция backward-compatible, старые state rows не трогать.

**M3 — fail-closed binder:** новый BG launch URL exact; UI verifier с реальным login; документная привязка; без UI proof = DENY.

**M4 — transactional Send:** адаптер authorization check непосредственно перед click; одна транзакция intent, один grant, zero replay после arm. Тесты на потерянный HTTP ACK, MV3 suspension, SPA navigation, Send click/reply race.

**M5 — final evidence:** Temporary opaque session receipt без обязательного /c/; однозначная последовательность authored turn + bound answer + Relay ACK, отделённый Telegram delivery ACK.

**M6 — CPU:** отдельно A/B профиль с реальными Edge Task Manager и PowerShell sampler, mutation-coalescer только после доказанного выигрыша.

**M7 — Windows E2E:** 1/2/5 вкладок и 60/120мин mixed session; отключение Hermes worker в середине, CDP detach, браузер window close; отсутствие повторного Send и неавторизованного tab.close. Результаты в PR.

**M8 — rollout:** opt-in feature flag default OFF, legacy route unchanged; ограниченная группа реальных задач, мониторинг, rollback без повторной доставки ARMED/UNKNOWN сообщений.

## 8. Текущий статус на 2026-11-22

Прототип `hermes_relay_bg_fail_closed.js` **реально открывает точный URL на уровне синтетического Chrome API**, но намеренно блокирует Get/Intent/Preflight/Result. Скрипты Edge запуска и диагностики не выполнены на клиентском Windows; настоящий Temporary UI не проверен; endpoints `v2/*` выше не существуют; интеграция не выполнена. Это статус **NO-GO**, а не «почти готовая» автоматизация.
