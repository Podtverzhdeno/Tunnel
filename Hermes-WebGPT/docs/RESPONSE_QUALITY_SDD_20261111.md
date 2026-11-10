# Hermes — SDD-дополнение: готовность инструментов и скорость ответа

Дата: 11.11.2026. Статус канонического пакета: **план подготовлен, требуется локальное подтверждение владельца**. Проверенные исправления ниже реализованы отдельно; это не формальная приёмка OpenSpec/SDD и не обещание безотказности.

## Строгий отбор

| Приоритет | Решение | Почему выбрано | Реализация |
|---|---|---|---|
| P0 | Исправить браузерный инструмент Windows и добавить read-only preflight | Воспроизведены неверный POSIX launcher и отсутствие Chrome; уже установлен Edge | Нативный exe + существующий Edge, без shell, загрузок и пользовательского профиля |
| P0 | Сохранять найденные публичные источники после tool turn | Данные исчезали после первого хода, что вынуждало повторять поиск | 12 записей / 300 с, точный hash последнего user request, defensive copy, без fetch на cache miss |
| P1 | Усилить существующий монитор очереди | Конкретные ошибки скрывались как other; большой возраст путался с зависанием | Известные reason codes, явный возрастной target, окно исходов, отдельные границы доказательств |

Дополнительно переиспользуется уже существующее направление `stabilize-hermes-tool-fallback-and-evidence`: fault-aware hints (T001–T004), проверяемый инструмент публичных источников (T005–T008), полноценные delivery receipts (T009–T012). Здесь не создаются второй transport, failure-hint engine или delivery ledger.

Следующий инструмент после стабилизации — типизированный `read_public_sources` по существующим T005–T008: фиксированные допустимые источники, retrieved_at/source_url/source_kind, fetched/verified, ограниченный timeout/cache и явный отказ. Пока он не зарегистрирован как новый инструмент Hermes. Заявление модели «я поискал» само по себе не служит доказательством.

Отложены: автоматическая установка произвольных скиллов/плагинов, параллельное увеличение числа браузерных агентов, ещё один Playwright runtime, агрессивное удаление system/user правил и слепой повтор Send.

## Архитектура и границы

Используются существующие Gateway, Relay, Web Companion и queue_monitor. Совместимость Windows включается проектным `gateway_bootstrap.py`; установленный Hermes-пакет не переписывается. Bootstrap сохраняет только PID, версии helper и тип выбранного launcher/browser. Явный AGENT_BROWSER_EXECUTABLE_PATH сохраняется. Реальный пользовательский браузер не перезапускается.

Кэш доказательств — только model view: исходные сообщения, результаты инструментов, схемы, tool_choice и canonical SQLite payload сохраняются. На followup отсутствующий кэш не вызывает сеть. Возраст отмечается явно; RSS-заголовок не превращается в прочитанную статью.

`browser_preflight.py` — доступный оператору CLI через существующий terminal. Это не автоматическая регистрация нового MCP/Hermes tool. Он проверяет зависимости, не делает navigation и не выдаёт проверку файлов за доставку Telegram.

Монитор читает прежние четыре поля SQLite в mode=ro. Новый каталог reason codes имеет версию: уточнение старого other не выдаётся за новый отказ. Целевой возраст не является длительностью стадии или доказательством смерти worker. Окно исходов считается по времени создания запроса, не завершения; done означает завершённый inference, не Telegram ACK и не правильность ответа.

## Требования и сценарии

### windows-browser-tool-readiness

Проверяемая готовность именно браузерного инструмента Hermes, отдельно от транспорта ChatGPT.

**FR-001: Native launcher selection.** The system SHALL replace only the known repository-local npm shim on Windows x64 with the verified installed native agent-browser PE executable, without invoking a shell or changing user arguments.

- **Known Windows shim:** GIVEN Hermes resolves its local POSIX npm shim and the native package executable is present; WHEN the project Gateway bootstrap starts; THEN the existing resolver uses one native executable argv item and records the actual Gateway PID and loaded helper hashes.

- **Foreign or invalid launcher:** GIVEN the resolver points outside the known package or the native PE/package identity is invalid; WHEN readiness is evaluated; THEN no executable replacement or arbitrary installation occurs.

**FR-002: Existing browser fallback.** The system SHALL preserve an explicit browser executable setting and otherwise select an existing verified Chrome or Edge executable without touching a user profile.

- **Edge installed:** GIVEN Chrome discovery fails and Edge is present in a known Windows installation location; WHEN the browser tool is bootstrapped; THEN AGENT_BROWSER_EXECUTABLE_PATH points to that existing executable and a controlled test uses its own session.

- **Explicit user choice:** GIVEN AGENT_BROWSER_EXECUTABLE_PATH is explicitly set; WHEN the compatibility helper runs; THEN the setting is preserved even if unavailable and no browser is silently installed.

**FR-003: Truthful activation evidence.** The system SHALL distinguish source installation, native CLI readiness, actual navigation, Gateway activation and Telegram delivery.

- **Loaded build:** GIVEN the Gateway was restarted during a verified idle window; WHEN activation is reported; THEN runtime PID and helper hashes match the running Gateway and Telegram connection state is checked.

- **Offline-only evidence:** GIVEN only unit tests or a CLI version probe succeeded; WHEN status is presented; THEN live browser navigation and Telegram delivery remain explicitly unconfirmed.

### followup-source-continuity

Ограниченный cache уже найденных публичных доказательств для следующих model-only ходов.

**FR-004: Bounded public evidence cache.** The system SHALL retain at most twelve public evidence entries for at most 300 monotonic seconds, keyed by the exact latest user request hash without storing raw private user text in the cache.

- **Same task tool followup:** GIVEN public evidence was obtained on the initial user turn; WHEN the same task returns a tool result within the TTL; THEN the model-only view reuses a defensive copy with evidence age and cached-no-network attribution.

- **Bound or expiry:** GIVEN an entry expired, the monotonic clock moved backwards or the capacity was exceeded; WHEN the cache is queried; THEN the stale or evicted entry is not reused and reads do not extend its lifetime.

**FR-005: No extra fetch or fabricated evidence.** The system SHALL perform no new source fetch on a tool-only followup cache miss and SHALL preserve source failures and headline-only provenance.

- **Cache miss:** GIVEN a tool followup has no matching evidence; WHEN the model view is built; THEN no network collection is triggered and no evidence is invented.

- **Insufficient source data:** GIVEN the original source collection returned no articles or timed out; WHEN the evidence is reused; THEN the same limitation remains visible and no claim of a new successful search is added.

**FR-006: Canonical request preservation.** The system SHALL keep canonical messages, real tool results, tool schemas and tool choice unchanged when adding model-only evidence.

- **User changes task:** GIVEN a newer or different user message exists; WHEN the model view is built; THEN the previous request's evidence is not reused.

- **Mutation attempt:** GIVEN a caller mutates returned evidence; WHEN a later model view is built; THEN the internal evidence cache and canonical request remain unchanged.

### response-quality-observation

Доработка существующего queue_monitor вместо второго сервиса: типы ошибок и явные временные цели.

**FR-007: Typed redacted failure categories.** The existing read-only queue monitor SHALL preserve supported browser/bootstrap/draft failure codes while mapping untrusted unknown reasons to other.

- **Known browser failure:** GIVEN a new request is blocked by content_tab_not_ready or tab_create_unverified; WHEN it is compared with the previous snapshot; THEN the precise known failure is reported as new without exposing user data.

- **Private reason text:** GIVEN a reason contains arbitrary text, a URL or credentials; WHEN a snapshot is emitted; THEN the value is replaced by other and never included verbatim.

**FR-008: Explicit latency target.** The existing monitor SHALL optionally report an active request exceeding a configured response-age target separately from a stale-state alarm, using creation age rather than claiming phase duration.

- **Slow active request:** GIVEN an active request exceeds the configured target but not the stale threshold; WHEN the monitor runs; THEN it reports a latency-target warning without declaring the worker dead or changing the request.

- **Completed request:** GIVEN a request is terminal; WHEN the monitor runs; THEN its creation age is not treated as current active latency.

**FR-009: Windowed inference outcome report.** The existing monitor SHALL report recent inference outcomes using an explicitly defined request-creation window, with unknown ratios when no terminal observations exist and without equating done with Telegram ACK.

- **Recent outcomes:** GIVEN done, blocked and uncertain requests started inside the selected window; WHEN the report is built; THEN it reports counts and the inference completion ratio with its scope and time basis.

- **No delivery receipt:** GIVEN only queue metadata is available; WHEN the report is built; THEN Telegram delivery, content correctness and p50/p95 phase latency remain unverified.

## Реализация и проверяемые результаты

| Область | Файлы | Доказательство | Граница |
|---|---|---|---|
| Windows browser | hermes_windows_browser.py, gateway_bootstrap.py | 20 Windows tests + 2 bootstrap; действующий Gateway подтверждён runtime receipt | Собственный пользовательский профиль браузера не используется |
| Реальная навигация | native browser smoke в лаборатории | Локальная HTML-страница: open 1,62 с; title 0,20 с; close 0,61 с | Это не измерение интернет-поиска или Telegram E2E |
| Public evidence reuse | model_news_prefetch.py, test_news_followup.py | 16 тестов; загруженный Relay SHA кэша c469f276… | Нет выдуманной свежести или подмены tool results |
| Read-only observers | browser_preflight.py, monitoring/queue_monitor.py | 5 preflight + 26 прежних и 12 новых monitor tests | Нет перезапусков, Send, записи SQLite и утверждения delivery |

Финальная проверка Windows: **188/188 PASS, без SKIP**, включая ранее существующие регрессии и актуальное параллельное добавление tool-health hints. Отдельный полный Telegram E2E этой сборки пока не подтверждён. Показатели p50/p95 и фактическую правильность ответа нельзя вывести из queue metadata.

## План выполнения и приёмки

1. [x] Воспроизвести WinError 193 и отсутствие Chrome; выбрать существующий native exe и Edge.
2. [x] Проверить launcher, реальную изолированную навигацию, загрузку helper в Gateway и подключение Telegram.
3. [x] Реализовать и активировать model-only кэш публичных источников; проверить TTL, ёмкость, смену задачи и неизменность canonical payload.
4. [x] Доработать существующий монитор и read-only CLI; проверить typed failures, upgrade baseline, временные цели и отсутствие утечки содержимого.
5. [x] Подготовить точный план OpenSpec/SDD: 10 новых файлов, 9 требований, 18 сценариев.
6. [ ] Получить foreground owner approval точного плана; выполнить штатный spec_request_execute и strict/crosswalk validation.
7. [ ] Провести серию новых Telegram E2E с подтверждённым API ACK и измерить полную задержку; не повторять неопределённые старые сообщения.
8. [ ] По существующему SDD направления T005–T008 спроектировать и зарегистрировать проверяемый public-source tool; T009–T012 — добавить фактический delivery ledger.

Формальное создание выполняется штатным Local Workspace workflow. Сначала spec_preview, затем spec_request_prepare, владелец подтверждает запрос командой `python owner_approve.py <request-id>` в своём Windows Terminal из Local-Workspace-Extensions. После approved требуется execute и настоящая strict-валидация. Черновик и выполненные тесты не заменяют это подтверждение.

