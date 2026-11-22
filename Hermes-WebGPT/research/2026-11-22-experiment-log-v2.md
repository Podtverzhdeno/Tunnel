# Hermes: дополнительный исследовательский цикл — протоколы вместо браузерного расширения

**Дата:** 22.11.2026. **Режим:** самостоятельные эксперименты с синтетическими данными. **Production:** неизменён. Не использовались реальные OAuth-токены, API-ключи, данные Telegram или пользовательские сессии.

## Новые подтверждённые инженерные факты

1. **Официальный SIWC является самостоятельным transport/auth, а не модификацией legacy `openai-codex`.** Публичный HTTP `POST https://api.openai.com/v1/responses`, только c отдельным `chatgpt.tokens.use.direct`. В `Podtverzhdeno/hermes-agent/hermes_cli/auth.py` legacy default `https://chatgpt.com/backend-api/codex`; эта связка не подтверждает права для публичного OSS SIWC.
2. **Нельзя считать пять OAuth-профилей пятью независимыми лимитами.** В текущей документации SIWC для Plus один общий пятиичасовой лимит приложений (для Pro описана отдельная модель). Необходим admission controller на все Hermes agents; пять логических сессий не доказывают пять параллельных завершений.
3. **WebSocket mode — отдельная гипотеза оптимизации, не first milestone.** При SIWC preview WebSocket continuation относится только к тому же authenticated socket; нельзя использовать его как долговременную persisted историю. App Server в примере SIWC явно настроен `supports_websockets=false` и работает через HTTP/SSE. Нельзя приравнивать эти режимы.
4. **Hermes native формат не совместим 1:1 с Responses.** `Hermes-WebGPT/relay.py` принимает `/v1/chat/completions` с `messages`, `tool_choice` и `choices[].message.tool_calls`. SIWC требует Responses `input`, `store:false`, `stream:true`, без запрещённых параметров; вызовы инструментов в `response.output` имеют `type:function_call`, `call_id`, `name`, `arguments`. Запрещено просто заменить URL.
5. **Потенциальный live-риск у fork Hermes:** в `run_agent.py/_run_codex_stream` при отдельных сбоях stream есть повтор запроса и fallback к `responses.create(stream=True)`. Если первая попытка уже была принята удалённой стороной, это не обеспечивает гарантию at-most-once. Данный путь требует отдельного audit/disable retry для нового маршрута, его не следует напрямую использовать как утверждённую гарантию эффекта.
6. **Независимые external effects:** модельный запрос, вызов инструмента и Telegram delivery требуют разных записей в durable ledger. Истинное exactly-once без серверного идемпотентного ключа/receipt не гарантируется. Квитанция от модели не является доказательством исполнения tool или доставки Telegram.

## Эксперименты v2

- `siwc_wire_adapter.py` — Python stdlib HTTP-клиент с пиннингом `api.openai.com/v1` и отдельным разрешённым loopback fixture, redacted repr токена, запретом редиректов, bounded SSE, проверкой `response.created`/`response.completed`, ошибками до и после потока. Не реализует OAuth и не должен использоваться с пользовательским токеном без дополнительной security review.
- `test_siwc_wire_adapter.py` — настоящий HTTP с loopback ThreadingHTTPServer, статусы 401/429/503, interrupted/failed stream, другой response ID, model discovery, отсутствие повторного вызова при reject reservation. **29 тестов**, включая локальный HTTP и security-проверки.
- `appserver_stdio_fixture.py` — дочерний Python-процесс и line-delimited JSON-RPC по stdin/stdout; normal/approval/crash/foreign. `test_appserver_stdio_fixture.py` — 5 проверок.
- `test_transport_integration.py` — 4 теста интеграции SQLite reservation и локального HTTP, включая рестарт и запрет replay.
- `effect_ledger.py` и `test_effect_ledger.py` — раздельные side-effect keys для inference/tool/Telegram, CAS/reserve/receipt, повторные попытки, 20 тестов, конкурирующие потоки. Это **не** execution-approval broker.
- `hermes_output_bridge.py` и `test_hermes_output_bridge.py` — перевод подтверждённой Responses-схемы в Hermes assistant/message/tool_calls, проверка доступных tool names и JSON, строгий запрет прямого tool execution. 16 тестов. Полная JSON-schema валидация инструментов Hermes остаётся отдельным интеграционным шагом.
- `browser_cdp_fixture.py` — локальный изолированный Chromium, синтетический DOM, один Send и сохранение чужого черновика после reconnect. Это доказательство возможности CDP на тестовой странице, не легальность/надёжность автоматизации ChatGPT Web.

**Фактически весь набор:** 128/128 пройденных unit/integration-lab tests. Это не 128 независимых real-model E2E. Отдельный CDP smoke PASS. Синтетический stdio использует дочерний Python-скрипт, а не настоящий Codex binary. OAuth/подписка/Windows Gateway/Telegram live E2E не тестировались.

## Обнаруженные и устранённые недостатки в прототипах

- Первоначальный приоритет legacy Hermes Codex выше официального SIWC — исправлен в экспериментальном selector; нужен реальный account entitlement для готовности.
- Убрана возможность без отдельного `allow_paid_api=True` выбрать paid OpenAI API как запасной вариант.
- Токен скрыт из dataclass `repr`; по умолчанию запрет HTTP-redirect, чтобы bearer не уходил на другое направление.
- SSE применяется строго один раз после durable reservation. Потеря окончательного event оставляет `uncertain`; никакой fallback/ретрай на новую модель.
- Убраны ResourceWarning при закрытии дочернего процесса и SQLite, сборка запущена с `-Werror::ResourceWarning`.

## Главные технические неизвестности (не выдавать за результаты)

- Выданный пользователю официальный OAuth client_id и подтверждённая область `chatgpt.tokens.use.direct`.
- Конкретный модельный каталог текущего аккаунта. Примеры названий моделей в документации не являются доказательством entitlement пользователя.
- Реальные возможности Hermes tool schemas при SIWC preview namespace/additional_tools и запрете Responses tool_search.
- Семантика Telegram ACK, подтверждённое сообщение/тема и отсутствие дубля при потерянном API-ответе.
- Статусы и PID текущей Windows установки: доступный Local Workspace показал только разрешённый `shelter`, не рабочую папку Hermes. Отсутствие workspace не означает остановку Hermes.
- Длительные испытания с реальным Codex App Server/инференсом, пять параллельных задач и статистика p50/p95.

## Что запускать в следующий раз на Windows (только в изоляции)

1. `py -3 -m unittest discover -s <LAB_DIR> -p 'test_*.py' -q` — без учётных данных, сеть только loopback.
2. `py -3 preflight.py` — инвентаризация CLI, не выводит пароли и токены.
3. Отдельный `HERMES_HOME`, отдельный контур Telegram/неproduction для app-server и SIWC с явным consent.
4. Сверка SHA установленного runtime и инструментов Hermes до запуска модельных испытаний.
5. E2E canary без tools, затем безопасный read-only tool и один искусственный отказ. Каждый переход должен быть подтверждён receipt, а не текстом модели.

## Релевантные первоисточники

- https://developers.openai.com/siwc/token-sharing-open-source/
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
- https://developers.openai.com/api/docs/guides/function-calling
- https://learn.chatgpt.com/docs/app-server
- https://openai.com/policies/terms-of-use/
- https://github.com/Podtverzhdeno/hermes-agent/blob/main/run_agent.py

**Вывод:** проверяемую возможность отказа от расширения подтверждают **официальный SIWC Responses** и **официальный Codex App Server** как интерфейсы, а не наши 128 unit-тестов. Тесты подтверждают лишь устойчивость подготовленного локального слоя для ограниченных сценариев. Следующий рубеж — подтверждённый consent и модельный E2E на конкретном аккаунте.