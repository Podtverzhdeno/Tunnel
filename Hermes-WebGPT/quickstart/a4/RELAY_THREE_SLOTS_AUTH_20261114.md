# Hermes A4 — authenticated Relay capacity & DEV readiness, 14.11.2026

## Результат

- Настоящий локальный Relay на `127.0.0.1:18765` при *авторизованном* read-only `GET /bridge/status` подтвердил `max_active_requests=3`. Отдельный файл конфигурации тоже содержит `max_active_requests=3`. Аутентификационный ключ считан локальным процессом только в память, **не выводился в отчёт** и не отправлялся в GitHub.
- Исходный тест: Local Workspace job `c0bb52d0d23b40269be7bf610e1a76b9`, exit 0: `local_relay_authenticated=true`, `running_relay_slots=3`, `at_least_three_slots_active=true`, `three_parallel_chat_requests_e2e_verified=false`, `bridge_send_verified=false`.
- Дополнена единая read-only DEV-диагностика `quickstart/a4/dev_status.py`: четыре локальные службы готовы, 28 инструментов MCP; Relay с проверенной ёмкостью 3. Контрольные флаги ChatGPT Web, Telegram и медиа остаются false. Job `21b7c3dab78a411aaa58ddf886500934`, exit 0.
- В интерфейсе мастера при нажатии **«DEV: проверить работающие службы»** отображается `Relay 3 слота`, но прямо указывается, что browser E2E не подтверждён.
- Реальный повторный DEV OneClick `Start-Hermes-All.cmd` после правок: `PASS`, exit 0, `service_and_receipt_identity_unchanged=true`, `web_companion_bridge_authenticated=true`, `autopilot_send_unverified_warning=true`, без отправок. Job `79efe43deaea4b1798d62729155f5a05`.
- Новые unit tests `test_relay_capacity_observer.py`: 7 total / 6 PASS / 1 SKIP (Windows symlink privilege). `test_dev_status.py`: 8 PASS. Общая Windows Quickstart регрессия: **230 total / 220 PASS / 10 SKIP / 0 FAIL**, returncode 0, job `2856483aba254deabc28f4e46221b030`.

## Граница доказанного

Подтверждена лишь **ёмкость 3 одновременных запросов Relay**, не успешная отправка трёх промптов через Yandex/AutoPilot. Транспорт A1 всё ещё блокируется `TRUSTED_WEB_COMPANION_NOT_CONNECTED`: в текущем `session_control_v1.mjs` `relayAdapter` по умолчанию null; ссылка на runnable Web Companion сама по себе не создаёт authenticated lease для A1. А2 не имеет утверждённого внешнего session API и A3 не подтвердил исходную отправку.

Ни один старый request/job не был повторно отправлен. Не создавалось новых вкладок и Telegram-сообщений; действующие процессы и политики не изменены. DEV продолжает запускаться без A4 production стоп-фактора.

## Следующая реально необходимая работа

A1/A3 реализуют удостоверенный binding к **оригинальному** Web Companion/Relay (исходный request_id, owner lease, active tab/session, persisted noReplay marker, provenance and ACK), затем проверяют **одну** отправку/ответ/Telegram receipt. Только после этого три полноценные сессии и медиа E2E. A4 принимает исходные release assets, проверяет восстановление после reboot и чистую установку из клона.
