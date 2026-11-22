# SDD / Hermes WebGPT — приём запросов, отказоустойчивость и десятки агентов

Дата: 2026-11-22. Статус: **изолированный draft для ревью; не развёрнут на Windows**.
Владелец существующего потока: `Hermes-WebGPT/relay.py` и каноническая очередь данного Relay. Обновление не пытается заместить upstream Hermes Kanban/Runs и не изменяет веб-отправку, пользовательскую авторизацию, Telegram или Chrome.

## 1. Что действительно установлено по исходникам

В текущем `Podtverzhdeno/Tunnel`:
- `Queue.submit` принимает до **5** незавершённых записей. Дедупликация поддерживает стабильный `turn_id` и неизменяемый маршрут.
- `Queue.claim` использует `BEGIN IMMEDIATE` и допускает **только 1** запись в `claimed/intent/saved` для обоих транспортов вместе, а не 5 конкурентных GPT.
- Подтверждение изменения внешнего ChatGPT состояния различает `claimed`, `intent`, `saved`, `done`, `blocked`, `uncertain`. Потеря связи после потенциального Send сохраняется `uncertain`, без автоматического повтора.
- `Server.submit_and_attach` ранее увеличивал число HTTP-ожидателей одного логического запроса без ограничения. В цикле ожидания каждый подписчик мог удерживать собственный поток до `request_timeout` (обычно до 1200 с). При `stream=True` дополнительно отправлялся SSE comment каждые ~250 мс. Таким образом десятки агентных повторов одного turn создавали много потоков/сокетов, **не увеличивая реальный throughput GPT**.
- `/bridge/status` уже отличает очереди и транспорты, но до изменения не раскрывал лимит ожидающих HTTP клиентов.
- Основной manifest расширения в репозитории v0.6.6, в README архивный пакет v0.8.5 и отдельный snapshot v0.9.7. Какая версия загружена в пользовательский Edge/YaBrowser на данном ПК — неизвестно.
- Local Workspace сейчас видит только разрешённый vault `shelter`, а не директорию `Tunnel` или реальные SQLite/PID. Работающий Relay, браузер, Telegram доставка и тайминги не наблюдались. Отдельная недоступность рабочего проекта не равна падению Hermes.
- Другие агенты заняли расширение (PR #10/#11), Web Companion bridge (PR #14), incident triage (PR #13), CDP transports (PR #7/#8). Изменения этой ветки ограничены `relay.py`, собственным test, CI и этим SDD.

## 2. Критический инвариант

**При большом числе агентов число подписок на один и тот же логический turn ограничено, а отказ новому подписчику не изменяет существующую очередь, её ownership или результат потенциального Send.**

```
50 клиентов Hermes
   -> входной шлюз с ограничением HTTP waiters (64 глобально, 8 на turn)
   -> Queue.submit(stable turn_id) [дедуп + max 5 pending]
   -> Queue.claim [1 active across both transports]
   -> Web Companion / user-authorized UI
   -> event: claimed -> intent -> saved -> done/blocked/uncertain
   -> stable result to accepted waiters
```

64/8 — **защитные начальные значения**, а не оптимизированная по измеренным p95 конфигурация. Редактируются при создании `Server`, валидируются; не передаются в ChatGPT prompt и не изменяют права веб-отправки. Сначала измерить p50/p95/99 latency и ресурсы, затем обоснованно настраивать. Для многопроцессной службы глобальный лимит ограничивает каждый server instance отдельно: не считать его кластерным quota.

При достижении лимита новый HTTP запрос получает JSON HTTP 429 с типизированным `http_waiter_capacity` или `turn_waiter_capacity` и `Retry-After: 1`. Это минимальная подсказка клиенту; реальный общий scheduler должен применять **jitter, exponential backoff, общий бюджет попыток и per-principal rate limit**, чтобы повтор через 1 с не создавал синхронный шторм. Пользовательский/клиентский успех и отказ не смешиваются. Поля `max_http_waiters`, `max_waiters_per_turn`, `active_http_waiters` отражаются в существующем статусе после аутентификации.

## 3. Что сейчас код гарантирует и НЕ гарантирует

| Вопрос | Статус | Основание |
|---|---|---|
| Нельзя записать 65-го HTTP waiter в один Relay при дефолте 64 | PATCHED, тестируется | проверка в `_waiters_lock` ДО `queue.submit` |
| 9-й подписчик того же turn не влияет на действующий запрос при дефолте 8 | PATCHED, тестируется | стабильный turn ID, отказ ПОСЛЕ нормализации, без отмены старых |
| В 50 параллельных запросах к одному turn допускается только заданное число waiters | PATCHED, тестируется | 50-worker synthetic contention |
| Ошибка 429 до постановки нового запроса в очередь | PATCHED, тестируется | no-orphan admission |
| Один браузерный Send на один подтверждённый запрос | НЕ доказано этой правкой | отдельные relay/bridge/extension guards; UI receipt и real E2E не выполнялись |
| 50 независимых одновременных GPT inference | НЕ реализовано | `Queue.claim` глобально single-flight |
| Межпроцессный общий waiter лимит | НЕ реализовано | ограничение локального процесса; SQLite очередь общая |
| Сервис восстановится после потери browser ACK без повторного Send | НЕ подтверждено в живом рантайме | требования noReplay, нужны crash tests |
| Быстрее общий ответ Hermes–Telegram | НЕ измерено | лимит подключения не уменьшает задержку inference |
| Повторный `turn_id` с изменённым payload не выполняется как новый | ПРЕЖНЯЯ гарантия | `turn_payload_conflict` |
| Тесты Python 3.11–3.13, Linux и Windows на HEAD этой ветки | CI GATE | отдельный workflow, результат читать только после завершения |

## 4. Шаг за шагом: где работа может остановиться

| Стадия | Отличительный сигнал | Возможные причины | Следующее допустимое действие |
|---|---|---|---|
| HTTP ingress | 401/403, socket close | principal, transport, incomplete body, shutdown | проверить auth и версию клиента; без эскалации прав |
| Admission | 429 + `http_waiter_capacity` | глобальное число открытых waiters | клиентский backoff с jitter, inspect metrics, не создавать новый turn |
| Admission | 429 + `turn_waiter_capacity` | дублированные подписки одного turn | использовать уже существующий waiter/terminal result, без повторной отправки |
| Queue submit | 400 `turn_payload_conflict` | изменён payload при стабильном turn id | считать конфликтом контракта, не менять исходное действие |
| Queue submit | 400 `queue_full` | пять pending, из них один может быть claimed | прочитать status, искать stuck state и не дублировать |
| Queue claim | null при queued>0 | глобально активная задача; workers могут быть в ожидании | read-only timeline, проверить active age, lease, bridge |
| Before Send | claimed -> intent отсутствует | передача, пустой редактор, устаревший документ | безопасный restart pre-effect по verified policy |
| After Send | intent/saved стареет | lost ACK, веб-клиент жив, ручное вмешательство, лимит | `OUTCOME_UNKNOWN`, сверить внешнюю историю, не click повторно |
| Model result | invalid reply JSON / tool args | неверный request_id, схема или провайдер | типизированный blocked/uncertain; никаких придуманных tool results |
| Telegram | Relay done, Telegram нет | outbound send failure, chat/thread mismatch | проверка отдельного receipt Telegram, не inference success |
| CI | cancelled | новая ревизия от конкурирующего агента, manual/infra | классифицировать superseded по точному HEAD/run |
| Local MCP | workspace доступен, Tunnel root отсутствует | scope restriction | не объявлять падение Hermes, не обходить allowlist |

Один heartbeat или HTTP200 `/health` не является прогрессом модели и тем более не подтверждает Telegram delivery. Предлагать причину остановки как гипотезу, отдельно указывать подтверждающий и противоречащий сигнал.

## 5. План 50-агентной системы без ложной параллельности

1. **Один канонический orchestrator**: использовать актуальные upstream Hermes Kanban/Runs (проверить совместимость с конкретной установленной ревизией) и маршрут к Relay, а не создавать второй независимый автомат с бесконечными повторными отправками.
2. **Отдельные роли:** исследователь, автор патча, репродуктор сбоя, security/permissions ревьюер, тестирующий E2E и release verifier. Разделять Git worktrees/PR, жизненный цикл и доступ к UI writer.
3. **Одна запись на внешний эффект:** `operation_id`, `turn_id`, `attempt_id`, actor, lease fence, target document/session, payload HMAC, durable reservation до Send, independently checked receipt после Send. Нет подтверждения — `OUTCOME_UNKNOWN`. Браузерное содержимое/аккаунт не экспортировать.
4. **Глобальный provider quota и fair queue:** per-worker лимиты не заменяют общую квоту. Не обходить ограничения сервиса ротацией аккаунтов.
5. **Два независимых измерения:** доступен ли worker и есть ли доказанный прогресс. Последовательные одинаковые tool fingerprints при активном heartbeat — лишь подозрение на цикл. Следить за `last_verified_effect` с exact code SHA.
6. **Version negotiation:** MV3 worker может выгружаться; `HELLO` с loaded extension manifest, worker incarnation, native host revision, Relay `runtime_build`; несовместимость — read-only fail.
7. **SLO после измерений:** p50/p95 time queued, claimed, intent, saved, inference, Telegram ACK; count per reason; max HTTP waiters, 429 rate, duplicate effect attempts, reconcile time. Сначала baseline, затем изменение budget/parallelism.
8. **Owner control:** Pause/Resume/Stop — отдельный запрос отмены и реальное подтверждение; эффект после click может сохраниться. Не навязывать автоматический reload или браузерное переключение.

## 6. Приоритизация идей: экспертная гипотеза, не измеренные результаты

Рубрика: impact 30%, safety 30%, compatibility 20%, testability 10%, operator clarity 10%. Каждый компонент оценивается 1–5 баллами; итог — средневзвешенная оценка x20.

| Идея | I/S/C/T/O | Балл /100 | Выбор |
|---|---|---:|---|
| NoReplay + сверка непонятного внешнего эффекта | 5/5/5/4/5 | 98 | P0, до массовых отправок |
| Authenticated causal logs и exact-SHA verifier | 5/5/4/4/5 | 94 | P0 |
| Сохранить единственный upstream Runs/Kanban lifecycle | 5/5/5/4/4 | 96 | P0 |
| Bounded HTTP waiters и читаемая HTTP 429 диагностика | 4/5/5/5/4 | 92 | реализован изолированный кандидат |
| Monitored progress vs heartbeat / loop detector | 4/5/5/4/5 | 92 | P1 |
| Общие retry budgets, concurrency fairness | 5/4/4/4/5 | 88 | P1 |
| Canonical UI/API version handshake | 4/5/4/4/5 | 88 | P1 |
| Bounded memory for 25 tabs and CPU profile | 4/4/4/4/4 | 80 | P2, не дублировать PR #10/#11 |
| Вторая независимая очередь с автоповторами UI | 2/1/1/2/1 | 30 | отвергнуть |
| Снять single-claim без lease+effect receipts | 3/1/1/3/1 | 38 | отвергнуть |

Это приоритизация по архитектурным свойствам; оценка не означает эксплуатационную надёжность 98%.

## 7. Инварианты и fault injection

- FI-01 — 50 одновременных HTTP клиентов с одинаковым `turn_id`: no more than configured per-turn limit, один queue row, нет дублей Send.
- FI-02 — достижение глобального limit 64: rejected before creating an orphan request.
- FI-03 — 429 при уже `intent`: ни отмены первого waiter, ни изменения lease, ни повтора Send.
- FI-04 — один waiter отменён: другой читатель продолжает то же логическое выполнение.
- FI-05 — last waiter отменён до Send: безопасный blocked; после intent: uncertain.
- FI-06 — 50 distinct turns: очередь ограничена до 5, не равно 50 inference.
- FI-07 — HTTP streaming 200 после исключения handler: нельзя писать JSON HTTP headers вторично; требуется отдельный SSE error contract, за рамками узкого изменения.
- FI-08 — Relay перезапуск с `claimed` и отсутствующим worker: read-only diagnose stranded slot; НЕ переводить автоматически в queued, если Send мог произойти.
- FI-09 — Relay перезапуск с `intent/saved`: запрет на автоматический новый Send и cross-transport fallback.
- FI-10 — замена браузерного документа между подготовкой и click: fail pre-effect.
- FI-11 — MV3 worker sleep между click и ACK: observe/reconcile before retry.
- FI-12 — чат пользователя вручную изменён: не очищать composer и не возобновлять без owner action.
- FI-13 — malformed provider tool schema или ответ чужого request_id: terminal non-success, не выдумывать.
- FI-14 — 429 от ChatGPT провайдера (не relay 429): отдельные throttling reasons и общий retry budget.
- FI-15 — full disk при effect-intent write: отказ до side effect, не click.
- FI-16 — old browser extension loaded after source changed: status stale loaded build; no live ready claim.
- FI-17 — live DB WAL меняется во время read-only мониторинга: no prompts, bounded read snapshot, explicit observation unavailable on corruption.
- FI-18 — изменение source HEAD во время CI: old-SHA pass not valid; canceled superseded distinct from failed.
- FI-19 — два агента одновременно редактируют background.js: isolated PR and compare SHA before update, no silent overwrite.
- FI-20 — Telegram delivery fails after Relay done: preserve separate delivery uncertainty, never claim full success.

**Наблюдение / acceptance:** Ветка добавляет offline HTTP+SQLite тесты 429/50 clients, маршрутизацию старого turn, safety after intent. CI на двух OS и трёх Python, плюс существующий backend suite. Это не Chrome/Windows live и не Telegram E2E. Перед релизом обязателен захват точного loaded SHA/версии, проверка текущего PID/порта/lease, локальный бэкап, отсутствие неизвестных Send и owner-approved canary с rollback. Не делать unconditional restart и auto-merge.

## 8. Лог расследования 2026-11-22

| Запись | Класс доказательства | Вывод | Действие |
|---|---|---|---|
| R01 | OBSERVED_SOURCE | `Queue.claim` single global active across transports | честно указывать capacity 1, не включать небезопасный parallel Send |
| R02 | OBSERVED_SOURCE | `submit_and_attach` без waiter limits; 250ms SSE wait comments | в отдельной ветке добавить admission budgets |
| R03 | PATCHED_UNVERIFIED | 64 global, 8 per turn, 429 on saturation, no orphan | новые тесты, exact-SHA CI, reviewer |
| R04 | OBSERVED_SOURCE | `/bridge/status` не имел Max HTTP waiter budget | добавить метаданные без payload |
| R05 | OBSERVED_SOURCE | persistent Relay requests/turn bindings and `uncertain` | сохранить эффекты, не изменять State machine |
| R06 | CONNECTOR_OBSERVATION | Local Workspace отвечает, но не включает Tunnel root | не заявлять live Windows monitoring |
| R07 | OBSERVED_GIT | PR #10/#11 пересекаются в background.js и popup; PR #13/#14 triage/bridge | не редактировать принадлежащие им файлы |
| R08 | RESEARCH | Chrome MV3 global variables may be lost on idle service worker termination | durable cursor, reconcile before action |
| R09 | HYPOTHESIS | Нескольких долгих HTTP ожиданий достаточно, чтобы исчерпать handler resources под нагрузкой | измерить sockets/threads/latency до и после |
| R10 | LIMITATION | Реальное число дублей Send и сохранение Telegram receipts не измерялось | live acceptance after approved source/install/loaded verification |

## 9. References and related work

- [Текущий Relay исходник](../relay.py)
- [Project requirements](../../docs/HERMES_FUNCTIONAL_SPEC_RU.md)
- [Queue monitoring](../../docs/HERMES_MONITORING_RU.md)
- [Source + installed deployment limitations](../README.md)
- [Concurrent bridge evidence PR #14](https://github.com/Podtverzhdeno/Tunnel/pull/14)
- [Concurrent causal monitoring PR #13](https://github.com/Podtverzhdeno/Tunnel/pull/13)
- [Concurrent browser UI PRs #10 and #11](https://github.com/Podtverzhdeno/Tunnel/pull/10)
- [Chrome service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/)
- [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging/)
- [Upstream Hermes Kanban worker lanes](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban-worker-lanes.md)
- [Upstream Kanban worker session observability gap](https://github.com/NousResearch/hermes-agent/issues/74008)

## 10. ADD-2026-11-22: SSE exception framing bug verified in source

Код `Handler.do_POST` до этой ветки мог отправить `HTTP 200` и `Content-Type: text/event-stream`, а затем при `ValueError/KeyError/TypeError/RecursionError` вызвать `self.error(...)`. После отправки заголовков второй HTTP response (например, `HTTP 400`) нельзя легально вставлять в SSE body; это невалидный stream и затрудняет RCA. Исправлено: флаг `stream_headers_sent` устанавливается сразу после end_headers; после этого typed/untyped ошибки передаются только как SSE `data: {"error":...}`, а необработанные внутренние сообщения редактируются как `invalid_request`. При закрытом канале прежнее поведение cancel/uncertain сохраняется. **Не отправлять [DONE] на ошибке.**

Новый тест с синтетической ошибкой `ValueError("PRIVATE_INTERNAL_EXCEPTION")` после SSE 200 проверяет отсутствие второго HTTP status line, отсутствие private exception, явный `invalid_request`, освобождение waiter и терминальное `blocked` до Send. Реальный браузер/Telegram не используется. Матричный CI повторяется на новом exact SHA; прохождение предыдущего CI этой ревизией не наследуется.

**Дополнительное ограничение:** низкоуровневые `sqlite3.Error`, `OSError` и внезапное завершение процесса в данном обработчике пока не переводятся автоматически в SSE: некоторые классы ошибок необходимо выявлять на уровне supervisor и durable ledger, не подменяя потерянный ответ выдуманным успехом. Это отдельная проверка, а не обещание решённого error universe.
