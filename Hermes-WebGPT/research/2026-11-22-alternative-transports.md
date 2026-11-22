# Hermes: независимые от расширения каналы взаимодействия
**Дата:** 2026-11-22. **Статус:** исследование / предложенная архитектура; не подключено к живому Gateway.

## Основное открытие

Для Hermes перспективнее отказаться от зависимости от web-среды как единственной системы инференса, а не изобретать очередной способ кликать по ChatGPT.com.

Проверены доступные исходники обоих репозиториев: Hermes-WebGPT в Tunnel и пользовательский форк Hermes Agent. В Hermes Agent уже реализован провайдер openai-codex, device-code OAuth, refresh и режим codex_responses, НО источник hermes_cli/auth.py устанавливает DEFAULT_CODEX_BASE_URL = https://chatgpt.com/backend-api/codex. Это НЕ документированный публичный OSS SIWC Responses endpoint и не должно механически заимствоваться для новой интеграции. Документация OpenAI отдельно называет Hermes Agent партнёром Sign in with ChatGPT, использующим при согласии владельца лимиты подходящей подписки. **Это НЕ доказательство, что partner login Nous Portal и текущий локальный провайдер openai-codex используют один и тот же согласованный OAuth-flow. Legacy backend требует отдельной проверки разрешённости; его не считать главным рекомендуемым production каналом.** Перед подключением надо изучить версию установленного бинарника и разрешения действующего аккаунта.

Существует официальный отдельный Sign in with ChatGPT для локальных open-source приложений: пользователь даёт разрешение chatgpt.tokens.use.direct, клиент закрепляет ext_agent_host_id и полученный OAuth client_id за пользователем/рабочей областью, использует штатные PKCE/state/nonce, а затем отправляет разрешённые запросы через публичный POST https://api.openai.com/v1/responses. Никакие приватные backend-API ChatGPT, cookie extraction или автоматическое извлечение из веб-чата не требуются. Поэтому **главный кандидат для собственного нового transport — официальный SIWC → публичный Responses API**, а форковый legacy openai-codex — лишь объект read-only диагностики.

## Сравнение вариантов

| Канал | Без расширения | Что предоставляет | Принципиальная граница |
|---|---|---|---|
| Native Hermes Codex (legacy fork) | Да | Встроенный provider/tool flow | По умолчанию chatgpt.com/backend-api/codex; не приравнивать к официальному SIWC |
| SIWC OSS + Responses | Да | OAuth по плану, streaming, программный ответ | Отдельное согласие, preview limits и локальная история |
| Codex App Server | Да | JSON-RPC, persistent threads, events, approvals | Не идентичен Hermes tool-calling, требует адаптера |
| Codex CLI | Да | JSON-события и независимый task runner | Процесс на запрос, lifecycle, контекст |
| OpenAI API key | Да | Поддерживаемый коммерческий inference API | Отдельная оплата, не эквивалент подписки |
| Локальный Ollama / vLLM | Да | Автономность от облачных UI | Другие модели, память/GPU и качество |
| Chrome CDP / WebDriver BiDi | Да | Управление вкладкой без расширения | По-прежнему web UI, правовые/DOM риски |
| Windows UI Automation | Да | Настоящие native controls Windows | Фокус/desktop session, не production inference API |
| Secure MCP Tunnel | Да | Доступ ChatGPT к инструментам ПК | Обратное направление, не запрос Hermes к LLM |

Пять логических сессий могут быть пятью независимыми Codex threads. Это не гарантия пяти параллельно генерирующих запросов — реальные usage quotas/throughput нужно замерять. И SIWC, и Codex не предоставляют доступ к существующей истории ChatGPT web или памяти пользователя. Веб High нельзя объявлять тождественным любому Codex model.

## SIWC: ограничения preview, влияющие на наш Relay

Поддерживаемый прямой поток — Responses API, с store=false и stream=true; подтверждение итога только по response.completed. Частичный текст до response.failed/response.incomplete не считается финальным Telegram-ответом. Не использовать previous_response_id, persistent conversation, temperature, top_p, max_output_tokens, metadata, background и другие неподдерживаемые preview-поля. Для каждого HTTP turn включать всю нужную историю в input; отдельные developer инструкции вместо явных system-items. Поддерживается ограниченный набор function/custom tools в разрешённой форме; нет hosted MCP, file_search, Code Interpreter, native computer_use или tool_search на этом маршруте. Учитывать фактические ограничения модели и владельческого workspace.

Отсутствие chatgpt.tokens.use.direct = отсутствие права отправлять по подписке; auth identity само по себе недостаточно. Ошибка 429 subscription_sharing_usage_limit_exceeded требует приостановки канала, а не обхода ограничений другой учётной записью. Нельзя незаметно переключаться на платный API ключ. Отозванные credentials следует считать недействительными; временный 503 сам по себе не доказывает отзыв. Для hosted/paid распределённых программ OSS-сценарий не предназначен без дополнительного согласования.

## Codex App Server: контракт до production

Установленный публичный протокол: initialize, initialized, thread/start или thread/resume, turn/start, потоки item/*, turn/completed. Все исходы привязывать к одному threadId, turnId, requestId и Telegram topic. Событие delta не равно завершению; статус failed или interrupted не равно выполнению. Команды и правки могут требовать item/commandExecution/requestApproval и item/fileChange/requestApproval. Нельзя генерировать owner approval из ответа модели либо без человека и независимого policy host. Выбранный sandbox и разрешённая директория должны быть проверены до запуска.

Программные adapters для native Codex, SIWC и App Server требуют отдельных contract tests на tool-call схемы Hermes, иначе возможны двойные tool execution и рассинхронизация памяти.

## Сохранить существующие гарантии

Hermes-WebGPT/relay.py уже содержит два веб-маршрута extension и playwright и alias web-chatgpt-high-cdp. Глобальный claim исходного Relay допускает один active claimed/intent/saved. Его схема для пяти элементов очереди не доказывает параллелизм. Спецификация durable-web-turn-provenance требует durable intent до Send, точной привязки страницы и ответа, и сохраняет UNCERTAIN, когда после попытки нельзя установить эффект.

Предлагаемый Router выбирает провайдера **перед новым ходом** по live capability/auth/account/model/policy/limit. Затем атомарно фиксирует turn_id, route, payload SHA256, owner, request_id; выполняет reserve_effect_once в локальной durable БД до внешнего эффекта. После неопределённого Send запрещён transport switching и auto retry. Exactly-once end-to-end без удалённой идемпотентности гарантировать нельзя. Отдельный Telegram delivery ledger должен подтверждать реальную отправку, а не только завершение model call. Оставить live Relay и старые uncertain requests неизменными при испытании новой ветки.

## Изолированные воспроизводимые эксперименты (2026-11-22)

Создан отдельный lab bundle, без приватных данных и каких-либо production modifications:
- route_guard.py: immutable binding, persisted at-most-once effect reserve, recovery, wrong chat, no post-intent replay — **19 unit tests PASS**.
- preflight.py: read-only обнаружение CLI и доступных бинарников, опционально лишь codex login status без печати credentials — **5 tests PASS**.
- codex_appserver_protocol.py: synthetic JSON-RPC lifecycle, exact thread/turn, incomplete events, no auto-approval — **11 tests PASS**.
- siwc_responses_protocol.py: unsupported body fields, store=false/stream=true, complete vs incomplete/failed, cross-response identity — **15 tests PASS**.
- browser_cdp_fixture.py: реальный локальный Chromium с отдельным temporary --user-data-dir, синтетический DOM, единственный fake Send, CDP disconnect/reconnect, ручной черновик другой вкладки сохранён — **PASS**.

Итого: **50/50 unit tests + 1 synthetic Chromium CDP smoke PASS**. Ни одна из проверок не обращалась к ChatGPT, OpenAI Responses, реальной авторизации или Telegram. На тестовом окружении codex/hermes CLI не установлены. Эксперименты по своим mocked contracts не равны conformance тесту поставщика.

В этот заход Local Workspace MCP вернул Session terminated. Живую версию на Windows, текущие PID, OAuth, Telegram delivery и соответствие установленного Relay GitHub SHA мы **НЕ проверили**. Не выдавать репозиторий или unit tests за live E2E.

## Измеримые acceptance gates

G0 — read-only inventory Windows: версии установленного Hermes, source SHA/loaded runtime, текущий Gateway статус; без вывода секретов.
G1 — read-only аудит legacy Hermes provider endpoint/авторизации, без попыток переделывать его в неофициальный SIWC. В отдельном HERMES_HOME проверить, доступен ли штатный поддерживаемый партнёрский путь, с отдельным owner consent; ни одна auth-cookieless гипотеза не означает права на legacy endpoint.
G2 — собственный официальный SIWC flow как основной кандидат: настоящий granted scope, проверенная модель, два безопасных последовательных turns, tool-followup и события failed/incomplete; no production writes.
G3 — App Server subprocess stdio: handshake, start/resume/interrupt, approval-required, crash/restart, cross-thread rejection, locked-down tools.
G4 — изолированная тестовая тема Telegram: один ответ, один tool-call, неизвестный ACK, exact conversation identity, отсутствие дублей.
G5 — пятисессионный прогон с реальными квотами и p50/p95; явные latency/error/failure metrics без выдуманных процентов.
G6 — версия и SHA фиксированы, backup, controlled rollout при idle и согласованные правила отката; ни один uncertain turn не переносится между маршрутами.

## Вопросы, которые нельзя решить без live Windows

Нужно установить реальную доступность официальной интеграции Hermes на текущей установленной версии, подтвердить добровольное право на использование плана и посмотреть поведение live Telegram с результатами конкретного account/model. Остальные действия (исследование архитектуры и изолированные тесты) не требуют вмешательства владельца.

## Проверенные источники

- https://learn.chatgpt.com/docs/sign-in-with-chatgpt.md
- https://developers.openai.com/siwc/token-sharing-open-source.md
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference.md
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations.md
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery.md
- https://learn.chatgpt.com/docs/app-server.md
- https://openai.com/policies/terms-of-use/
- https://github.com/Podtverzhdeno/hermes-agent/blob/main/website/docs/integrations/providers.md
- https://github.com/Podtverzhdeno/hermes-agent/blob/main/hermes_cli/auth.py

**Самооценка:** защищённый лабораторный стенд и сравнительный анализ есть; **0 нового live Telegram E2E в данном заходе**. Этот документ сохраняет исследовательские решения и не является разрешением обходить существующие protected host approval/broker механизмы.