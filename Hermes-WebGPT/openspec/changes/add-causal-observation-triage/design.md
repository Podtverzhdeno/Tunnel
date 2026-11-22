# SDD — причинная диагностика Hermes-WebGPT, Relay и GPT/браузерных исполнителей

**Статус:** 2026-11-22; самостоятельный read-only компонент с офлайн-тестами. Реальная интеграция в браузер, Gateway и Windows-процессы пока не активирована.
**База:** Podtverzhdeno/Tunnel, main 350ea33080bc35245aa6741f85fa926cc8a8fcaf.
**Принцип:** наблюдение ≠ гипотеза ≠ доказанная причина ≠ проверенное завершение.

## 1. Фактическая архитектура и владельцы

Рабочая схема по доступным исходникам: Telegram → Hermes Gateway → Hermes-WebGPT/relay.py → очередь Relay со состояниями queued/claimed/intent/saved/done/blocked/uncertain → Web Companion / авторизованный CDP-адаптер → ответ, связанный с точным request_id → Relay → Gateway → Telegram.

Реальные владельцы подсистем:
- **Relay:** Hermes-WebGPT/relay.py, уникальность запросов, сохранение intent, noReplay, верификация результата.
- **Web Companion:** chat-launch-smoke-desktop-test/web-companion; браузерная копия chat-launch-smoke-desktop-test/web-extension. Исходник main manifest 0.6.6. Документация описывает другую установленную локальную сборку 0.9.7, поэтому SHA загруженного расширения неизвестен, переносить main поверх него нельзя.
- **Read-only очередь:** Hermes-WebGPT/monitoring/queue_monitor.py. Выбирает из SQLite только id/state/reason/created; хеширует идентификатор; каталог причин v3. Состояние done не доказывает доставку Telegram.
- **Интерпретация инцидентов (новое):** Hermes-WebGPT/monitoring/incident_triage.py. Читает исключительно очищенный JSON queue_monitor и не имеет доступа к Relay, браузеру, файловым изменениям и Send.
- **Глобальный событийный observer:** отдельный репозиторий Podtverzhdeno/hermes-agent, draft PR #9; пока не интегрирован сюда.
- **Контроль задач и внешних действий:** hermes-agent draft PR #1, отдельная SQLite модель approvals/effects/fences. Не заменять её новым исполнителем.
- **Параллельные разработчики:** в Tunnel имеются PR по Edge lifetime/CPU, CDP транспортам, восстановлению сессий и проверке результатов. Не редактировать файлы их текущей ответственности без согласования владения.

Исследование исходников подтверждено через GitHub. Local Workspace MCP завершил сессию и не дал прочитать текущий Windows runtime; реальный GPT и Telegram E2E не выполнялись. Заявлять обратное недопустимо.

## 2. Главная причинная проблема

Один код "тайм-аут" может означать медленную модель, ожидание инструмента, остановленный MV3 service worker, потерю CDP или ACK после фактически выполненного Send. Если заменить эти состояния одним retry, возникнет второй запрос с неопределённым результатом, сдвиг очереди, дублирование внешних операций или потеря исходного контекста.

Восемь независимых сущностей: логический task; request; попытка; worker и его поколение; браузерный target/tab; авторизованное внешнее действие; ожидаемый отчёт; получатель доставки. Переиспользовать их идентификатор как общий "session" нельзя. Поздний ответ агента предыдущего поколения не должен закрывать инцидент текущего агента; reattach одного target не должен закрывать detach другого; reconciliation одного action не должен закрывать другой.

Минимальная будущая цепочка доказательств: immutable task + repo SHA → авторизованный lease/fence → конкретный request + attempt → persist intent → один Send → убедительный saved receipt, привязанный к правильному чату и request_id → проверенный полный ответ → журнал delivery Gateway/Telegram. На каждом переходе хранить независимую причину неопределённости, а не подменять её "успехом".

## 3. Что реализуется этой веткой

Новый incident_triage.py — чистое преобразование JSON queue_monitor в **только рекомендательный** структурированный диагноз. Он не создает таблицы, не запускает сервис, не пишет baseline, не читает приватные поля SQLite, не анализирует DOM, не получает токены и не повторяет запросы.

Вход: observation=available, scope=queue_metadata_only, счётчики состояний и ограниченный перечень alerts; или observation=unavailable с кодом ошибки. Каталог reason code импортируется из существующего queue_monitor; произвольные причины, неизвестные типы и смешанные ключи отвергаются либо обобщаются. Вывод: status, confidence, issues, severity P1–P3, safe_next_step, counts, missing_evidence, ограничения доставки/верификации.

Статусы: **no_queue_alerts_observed** — нет тревог в метаданных (НЕ "здоров"), **attention_reported** — есть основания исследовать проблему, **observation_unavailable** — наблюдение недоступно (НЕ "всё остановилось"). **safe_auto_recovery=false** в любом случае. **verified_task_completion=false** и **telegram_delivery_verified=false** при доступных метаданных.

Порядок приоритета: неизвестный итог внешнего действия / неопределённый Send (P1) → новые блокировки, authentication, старый активный запрос (P2) → age target и исторические блокировки (P3). **Исторический uncertain остаётся P1, даже когда baseline не считает его новой ошибкой**. Слабый код reason сообщает категорию для первого безопасного исследования, но не подтверждает корневую причину.

## 4. Гипотезы, контрсвидетельства и границы восстановления

| Наблюдение | Несколько возможных причин | Безопасное действие | Что запрещено выводить |
|---|---|---|---|
| uncertain_send / uncertain state | Send выполнен и потерян ACK; цель изменилась; транспорт отвалился | Сверить exact request/intent/chat receipt, не отправлять снова | "Запрос точно не ушёл" |
| blocked send_unconfirmed_timeout | UI мог отправить prompt; подтверждение отсутствует | Изучить saved turn и reply binding | "Можно повторить Send" |
| manual_input | ручной ввод пользователя или свой старый черновик | Исследовать provenance и историю вмешательства | "Пользователь точно редактировал" |
| login_required | истёк вход, сменилась сессия, ошибочный target | Настоящая owner-authentication flow | собирать cookies |
| rate_limit | лимит/квота провайдера | проверить Retry-After, лимиты очереди | бесконечный retry |
| editor_not_found | изменился DOM, не та вкладка, страница не готова | read-only preflight правильного браузера | повторные догадочные клики |
| старый claimed или saved | backlog, медленная модель, нет свежих событий | независимое сравнение Gateway, Relay, worker | "worker умер" |
| database_unavailable | упал монитор, нет MCP, неверный путь, иной deployed build | вернуть read-only наблюдение | "очередь пуста" |
| отсутствие alerts | монитор не видит ошибку | отдельно проверять Gateway/Telegram | "система исправна" |
| main extension 0.6.6 vs локальная документация 0.9.7 | разные релизы и source overlay | сверить фактический загруженный hash | заменять локальную установку кодом main |

OpenSpec already describes pre-Send durable intent and no ambiguous-send retry (add-playwright-cdp-web-transport/specs/durable-web-turn-provenance). Изменение НЕ ослабляет этот контракт.

## 5. Многоагентность и атомарность

Десятки агентов допустимы только при явном назначении владельцев и ограничений доступа. Писать один SDD в main параллельно запрещено: отдельная ветка на задание, чтение текущего Git SHA перед обновлением, конфликт вместо last-writer-wins, ревью изменений. Любой worker получает scope, revision, budget, expected artifacts, точные критерии приёмки. Replaced workers могут продолжать только с ledger/checkpoint и новым fence; они не наследуют разрешения предыдущей сессии.

Для будущего сквозного журнала использовать event_id, task_id, request_id, run_id, step_id, producer_id, agent_id, attempt, fence, target_id, action_id, received_at, status code, subject revision. Producer ID отдельный от роли A0/A1...; запись атомарна до ACK. Receipt order ≠ причинный порядок между производителями; W3C traceparent + parent step ID дополняют поля. Не хранить prompt, ответ, DOM, cookie, CDP URL, user email или raw tool args.

Штатные Chromium MV3 фоновые service workers могут выгружаться; debugger может detach с target_closed/canceled_by_user. Результат браузерной интеграции — диагностический сигнал, не доказательство завершения модели. Авторизация исходящих действий и exact-effect reservation должны проверяться в отдельном доверенном tool gateway; telemetry не наделена правом выполнить действие.

Для реального внешнего write: сначала записать точное одобрение и намерение/резервирование, затем выполнить ровно один авторизованный вызов, затем сверить receipt. Если вызов мог состояться, но ACK потерян — **needs_reconciliation**, а не retry. Безопасный повтор разрешён только для доказанного pre-effect отказа или при защищённой идемпотентности конкретного провайдера.

## 6. Уровни доказательств и SLO

1. Code available / test passing: исходники и synthetic assertions; НЕ deployed.
2. Installed source: проверена версия и SHA локально.
3. Loaded runtime: сверены PID, launch arguments и хеш фактически загруженного процесса/расширения, не только файлов на диске.
4. Browser interaction: единственный разрешённый Send, верная вкладка/чат/request и post-send evidence.
5. Relay result: привязанное и проверенное окончание ответа, tool schemas.
6. Gateway receipt: реальный transport result.
7. Telegram delivery: подтверждённый API ACK в исходном чате и теме.
8. Business success: отдельная независимая проверка результата/коммита/CI точного SHA.

Не сворачивать уровни в одно DONE. Текущий queue monitor покрывает только часть уровня 1 и ограниченные признаки Relay; новая triage подсистема ничего не добавляет к привилегиям. Заявление "проверены 29 инструментов" или "39/39 тестов" не подтверждает реальную доставку.

Предварительные, не измеренные целевые показатели: 100 конкурентных synthetic producers без смешения identity/action/target; ни одного автоматического дублирования неопределённого Send; p95 collector-to-query <2s при измеренном локальном профиле; оператор отличает unknown от failure; recovery после MV3 перезагрузки с replay того же event_id; точный loaded SHA каждого production релиза. Замерять очередь, latency, errors, saturation и расходы на верифицированный результат; не выдумывать текущие метрики.

## 7. Этапы и gates

**P0, эта ветка:** read-only triage поверх существующего monitor, отдельные тесты, OpenSpec/SDD. Локально тестированы synthetic сценарии, **не** E2E.

**P1:** проверить фактическую установленную версию Web Companion на Windows, live Relay schema, PID и loaded SHA. Повторить real queue monitor и regression suite на оригинальной машине после восстановления Local Workspace.

**P2:** authenticated collector с persistent inbox, ACK-after-commit, privacy и bounded backlog. Проверка реального события start/end per step, источник криптографически/процессно привязан к разрешённому adapter. Подключить event envelope к hermes-agent PR #9 только после review.

**P3:** связать exact-action approvals/effects/fences с доверенным ledger (PR #1), предоставить independent verifier и noReplay. Никаких глобальных полномочий расширению.

**P4:** owner-consented MV3/CDP diagnostics; испытания закрытия вкладок, stop, crash, rate-limit, stale worker, altered DOM, disconnected tunnel и реальной доставки. Rollback, backup, retention, observability UI.

Не начинать production-активацию, пока нет доверенного producer ID, verified runtime hash, прав на browser API, провайдера external-effect receipts и возможности технически объяснить причину остановки без чтения приватного chat.

## 8. Строгая приёмка

Каждое исправление должно приложить точный incident (sanitized IDs), наблюдение, альтернативную гипотезу, доказательства и опровержения, исходную SHA, минимальный patch, тест до/после, аудит безопасности, план rollback, отдельный verifier. Если задача пересекает файловый scope другого агента — ставить конфликт владения, не перезаписывать. Код с невалидным reason/count/age должен fail closed. Нет output "healthy", "verified" и auto-replay из одной очереди.

Документация standing authorization в Hermes-WebGPT/AGENTS.md **не является** полномочием подделать terminal approval: существующий broker должен выдать реальное разрешение на gated action.

## 9. Источники

- Локальные источники репозитория: Hermes-WebGPT/relay.py, monitoring/queue_monitor.py, monitoring/test_queue_monitor.py, docs/SEND_CONFIRMATION_FIX_20261111.md, docs/RESPONSE_QUALITY_SDD_20261111.md, docs/HERMES_MONITORING_RU.md, спецификация durable-web-turn-provenance.
- Chrome MV3 lifecycle: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/
- Chrome debugger onDetach: https://developer.chrome.com/docs/extensions/reference/api/debugger/
- SQLite WAL: https://www.sqlite.org/wal.html
- OpenTelemetry GenAI agent/tools: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/execute-tool.md
- W3C trace context: https://www.w3.org/TR/trace-context/
- Google SRE monitoring: https://sre.google/sre-book/monitoring-distributed-systems/
