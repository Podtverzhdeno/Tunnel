# ADR-0001: Долгосрочные альтернативы расширению для Hermes

**Решение:** ACCEPT FOR LAB / NOT APPROVED FOR PRODUCTION. Дата: 2026-11-22. Приоритет проблемы: P0, высокая сложность, недопустимы оптимистичные предположения об авторизации, лимитах, точности tool calls и доставке. Это решение не меняет текущие Gateway, Relay, Windows-сессии и Telegram.

## 1. Задача и необходимый результат

Действующая цепочка Hermes ↔ Web Companion ↔ ChatGPT UI ↔ Telegram должна постепенно получить **официальный разрешённый программный маршрут**, при котором Hermes остаётся оркестратором, а браузерное расширение перестаёт быть единственной точкой отказа. Требуемые свойства: пять логически независимых агентных сессий, правильный account/model, поддержка вызова собственных tools с отдельным approval, устойчивая привязка к логическому ходу, потоковый ответ с конечным доказательством, отсутствие дубликатов, отдельная Telegram-доставка, диагностируемое восстановление и сохранение существующей Windows-установки.

Критическая граница: **создание новых разговоров через официальный API не даёт продолжать существующие URL разговоров ChatGPT Web, брать их историю и пользоваться модельным режимом High как идентичным API-профилем**. Если абсолютное сохранение одного веб-диалога обязательно — новый официальный API транспорт не может заменить это требование; ручной веб-доступ остаётся отдельным сценарием.

## 2. Решение по каждому каналу

| Вариант | Зависит от расширения? | Независимость от ChatGPT Web? | Способ подписки/оплаты | Пригодность для Hermes |
|---|---|---|---|---|
| **SIWC OSS → public Responses HTTP/SSE** | Нет | Да | Отдельный согласованный `chatgpt.tokens.use.direct`, общие лимиты | **P0 основной кандидат**; нужен compatibility relay и OAuth provider |
| **Codex App Server (stdio + HTTP/SSE)** | Нет | Да | При настроенном SIWC access token | **P1 для coding/agent sessions**; thread/resume, approvals и tools требуют отдельного адаптера |
| Codex CLI/SDK | Нет | Да | Через поддерживаемую авторизацию | P2 для контролируемых пакетных задач; запуск/состояния/привилегии отдельно |
| Native Hermes `openai-codex` legacy | Нет | Да, но на собственном backend | В форке default `chatgpt.com/backend-api/codex` | Только исследовать; не выдавать за публичный SIWC |
| OpenAI API key → Responses | Нет | Да | Отдельный API-биллинг | Допустимо только при явно выбранном платном режиме; не silent fallback |
| Ollama/vLLM локально | Нет | Да | Локальные вычисления | Действительная независимость, но иные модели/качество/ресурсы |
| Responses WebSocket | Нет | Да | Провайдерный режим; preview ограничения | P2 оптимизация после HTTP E2E, не стойкое хранилище беседы |
| CDP / WebDriver BiDi / UIA | Нет | Нет | Web UI подписка/сессия | Неинференсный лабораторный механизм; проблемы UI/права автоматической выгрузки |
| ChatGPT custom MCP/Tunnel | Нет | Не заменяет inference | Используется ChatGPT как клиент | **Обратное направление**; Telegram не может сам инициировать новый ChatGPT model turn через инструмент сервера |
| Agents API / cloud harness | Нет | Да | Собственная API-продуктовая модель | Возможность для удалённого выполнения; сравнивать только после приоритетного сценария SIWC |

**Выбор:** создать новый локальный **Hermes SIWC Compatibility Relay** на отдельном loopback порту и отдельной SQLite/конфигурации. Он принимает Hermes ChatCompletions-подобные вызовы, нормализует их в **публичный** SIWC Responses request и возвращает совместимую Hermes структуру. Не менять старый `relay.py` и не выполнять миграцию `uncertain` сообщений. Изоляция нужна, потому что общий старый Relay имеет особые состояния web Send и global-claim, которые не эквивалентны HTTP inference.

## 3. Почему нельзя «просто сменить base_url»

Проверенный `Hermes-WebGPT/relay.py` ожидает `POST /v1/chat/completions` и роли `system/developer/user/assistant/tool`, хранит `choices[0].message` и Hermes tool calls. Официальный SIWC принимает `POST /v1/responses`, `input` и `instructions`, `store=false`, `stream=true`; явный `system` в input запрещён. Запрещены в preview: `previous_response_id` для HTTP, `temperature`, `top_p`, `max_output_tokens`, `metadata`, `background`, `conversation`, `tool_search`, hosted MCP, Code Interpreter и ряд других инструментов.

Нужны **две стороны преобразования**: 

- `Hermes messages → Responses`: системные инструкции переносить в разрешённую форму, сохранять последовательность, `assistant.tool_calls` ↔ `function_call`, `tool.tool_call_id` ↔ `function_call_output`. «Осиротевшие» результаты и чужие IDs блокируются до внешнего эффекта.
- `Responses → Hermes`: ждать `response.completed` с правильным ID, нормализовать `response.output` в `assistant.content` или `tool_calls`. Проверять имена инструментов только из предложенных Hermes, JSON arguments и отдельный реально действующий Hermes schema-guard. **Никогда не исполнять tools из нормализатора**.

Схема передачи Hermes tool definitions на SIWC через Namespaces / `additional_tools` зависит от текущего provider preview и требует *живого conformance*. Пока разрешён **text-only lab**, а tool-bridge является моделью контракта, не разрешением на исполнение.

## 4. Внеполосные гарантии и уровни доказательств

```
Telegram update —[owner allowlist]→ Hermes Gateway
   ↓  (logical turn ID; account + session + chat/topic)
Route policy —[eligible model & grant; selected before effect]→ turn binding
   ↓  (atomic SQLite CAS)
Effect ledger —[reserve exactly one outbound attempt]→ SIWC / App Server
   ↓  (authenticated provider stream)
Typed output → [response.completed + ID + valid tool pairs] → Hermes loop
   ↓  (separate owner/tool authorization, no model self-approval)
Telegram delivery ledger —[Telegram API message_id]→ delivered
```

Отдельные состояния должны сохраняться для: **inference**, **tool side effect**, **Telegram send**. Один успешный inference не подтверждает ни tool execution, ни Telegram delivery. При потерянном ACK удалённого эффекта его нельзя повторять, пока нет документированного server-side idempotency или несомненного pre-effect отказа. Честный контракт — at-most-one local attempt и `UNCERTAIN` при неоднозначности; «exactly-once» по одному локальному журналу недостижим.

Старые запросы web-extension остаются на своём original transport без пересылки в SIWC. Функция automatic SDK retry должна быть отключена для новой попытки внешнего эффекта или быть ограничена доказанным pre-effect failure. В старом Hermes fork `run_agent.py` явно существует retry на повреждённый response stream и fallback `responses.create(stream=True)`; это не переносить без проверки.

## 5. Безопасность и учётные записи

- Новый OAuth flow: открытый SDK/официальная документация, stable `ext_agent_host_id`, issued `client_id`, state/nonce/PKCE, проверка ID-token signature/issuer/audience/nonce, проверка разрешения `chatgpt.tokens.use.direct`. Login сам по себе не даёт право на inference.
- Рабочий токен хранится в защищённом локальном хранилище с минимальными ACL; не в source, Obsidian, логах, копиях Gateway, markdown или GitHub. Ротацию refresh сериализовать для одного issued client ID. Разрыв после refresh и до persistence может потребовать новую авторизацию; не прятать это автоматическим обходом.
- Model discovery — **GET `/v1/models` на выбранном аккаунте**; список динамический и не является стопроцентным proof возможности конкретного turn. Нет hardcoded выбранной модели без entitlement.
- Для 5 сессий **общий account admission controller**, без наивного умножения квот. Для Plus документация указывает общий пятиичасовой лимит между подключёнными приложениями, Pro отличается. Ошибка `subscription_sharing_usage_limit_exceeded`: пауза новых запросов на этом аккаунте, а не автоматическая смена учётных записей.
- Отдельный внешний paid API — только после явного согласия с биллингом; возвращать `not_available` или ставить запрос на паузу по умолчанию.
- Защищённый Windows P085 approval/broker не заменяется Boolean `approved`, модельным текстом или флагом конфигурации. Для command/file tool нужны независимые разрешения.
- Web automation как производственный inference transport не рекомендован: условия потребительских сервисов запрещают автоматическое извлечение output, а DOM-среда не даёт устойчивого протокола.

## 6. Набор экспериментов и честные выводы

**157/157** локальных unit и lab integration checks, Python 3.13 Linux, стандартная библиотека и настоящий loopback HTTP; отдельный synthetic Chromium/CDP smoke PASS. Старые 50 сценариев сохранены и дополнены следующими независимыми компонентами:

- bounded SSE parser / post-reservation HTTP errors, 401/429/503, partial stream without completion, чужой response ID, отсутствие redirect и token-in-repr;
- JSON-line stdio child-protocol (не реальный Codex binary): normal, crash, owner approval, foreign thread;
- durable SQLite effect reservation + multiple kinds: inference, tool, Telegram, restart, parallel claims;
- Responses→Hermes `tool_calls` proposal normalizer (только данные, не execution);
- Hermes history→Responses `instructions/input/function_call_output` converter (строго парные IDs);
- пять логических сессий на общем admission controller: fairness, backpressure, quota pause, отсутствие silent paid fallback;
- синтетический Chromium: CDP reconnect и сохранённый посторонний черновик без доступа к ChatGPT.

**Не проведено:** реальный SIWC owner login, настоящее OAuth scope, модельная генерация, five-session load, real Codex App Server бинарник, Telegram E2E, Windows deployment. Нельзя писать «157 E2E» или считать результаты доказательством production readiness.

## 7. Гейты приёмки и решение о внедрении

| Этап | Gate | Подтверждение |
|---|---|---|
| G0 | Инвентаризация Windows + installed/current SHA и runtime | read-only диагностика; доступ к папке Hermes, сейчас не подтверждён |
| G1 | Официальный SIWC OAuth, scopes, выбор профиля и модельный каталог | validated ID token, active granted scope, catalog display |
| G2 | Один реальный no-tool turn | полный stream, response.completed, точная модель, receipt, no replay |
| G3 | Два tool-followup turns без опасных действий | сохранённые call IDs, schema-guard, результаты, отделённые approvals |
| G4 | Codex App Server thread/resume/restart/approval | реальный binary, no auto-approval, verified thread IDs |
| G5 | Telegram canary | модельный ответ ≠ доставленный; отдельное API ACK и отсутствие дубля |
| G6 | Пять сессий и quota failures | реальная p50/p95, err-rate, priority fairness, verified quota handling |
| G7 | Controlled rollout | backup, source/runtime SHA, idle owner window, isolated rollback |

Пока не пройдены **G0–G3**, нельзя заменять рабочий provider. Сохранить возможность отключить новый transport и вернуть старый без миграции незавершённых запросов.

## 8. Сложность и устойчивое требование

**Это не лёгкая интеграция HTTP-клиента. Это сложная система согласования авторизации, транспорта, агентного состояния, прав инструментов и внешних эффектов.** Не оценивать успех по числу прочитанных статей, unit-тестов или краткости реализации. Каждая гипотеза требует независимого proof, а каждый отказ — наблюдаемого состояния и запрещённого replay. Это правило сохраняется в проектной документации для последующих агентных заходов.

## Источники

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
- https://developers.openai.com/api/docs/guides/function-calling
- https://learn.chatgpt.com/docs/app-server
- https://openai.com/policies/terms-of-use/
- https://github.com/Podtverzhdeno/Tunnel/tree/main/Hermes-WebGPT
- https://github.com/Podtverzhdeno/hermes-agent/blob/main/run_agent.py

**Status conclusion:** официальный путь без расширения обоснован документацией; практическая замена живого Hermes пока не доказана. Утверждение «готово 10/10» преждевременно.