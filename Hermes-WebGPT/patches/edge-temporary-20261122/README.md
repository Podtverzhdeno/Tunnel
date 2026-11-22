# Hermes Temporary Chat v2 — настоящий транспорт Web Companion 0.9.7 (проверочный стенд)

**СТАТУС: ИЗОЛИРОВАННЫЙ, НЕ УСТАНАВЛИВАТЬ В ЖИВОЙ HERMES.**

Эти файлы — проверочный прототип изменения **настоящего Hermes** (не старого `chat-launch-smoke-desktop-test`). Базовый однофайловый source snapshot: `Hermes-WebGPT/patches/manual-draft-20261110/hermes_relay_bg.js` (Git blob SHA `9f829b0f196d93622734a313c54b960086f1f898`). Вся установленная версия расширения на Windows может отличаться; текущие содержимое, auth, manifest, профили и установочные настройки не доступны этому PR.

## Что уже продемонстрировано кодом

- Экспериментальный `hermes_relay_bg_fail_closed.js` создаёт вкладку с **точным URL** `https://chatgpt.com/?temporary-chat=true` вместо `https://chatgpt.com/#hermes-relay=<request_id>`.
- **Передавать задание в браузер запрещено:** `hermes-relay-get` всегда отвечает `temporary_transport_v2_not_integrated`.
- **Автоматическая отправка также заблокирована независимо от get:** `hermes-relay-intent` и `hermes-relay-preflight` возвращают ошибку; `done` отвергается до установки receipt-v2. Это двойная защита от случайного Send при частично интегрированном коде.
- Вызов `chrome.tabs.remove` существует в исходном BG для legacy-задач, но новая цепочка `done` в этом стенде недоступна. Поэтому тест Temporary path не закрывает вкладку. В production-v2 эту ветку необходимо удалить или заменить явной release policy.
- Не меняется queue claim, noReplay fence, сохранение состояний. Новый вариант **не** принимает содержимое запроса в новый маршрут результата.

Этот файл **НЕ является готовым расширением**: не содержит `manifest.json`, runner и Relay v2. Если загрузить его как замену рабочего BG, реальные задания остановятся без Send. Это сознательный отказ, а не готовая реализация.

## Тестовый контракт

```sh
node Hermes-WebGPT/patches/edge-temporary-20261122/test_hermes_bg_fail_closed.cjs
```

Синтетическая фоновая среда без сети проверяет: ровно одну вкладку, точный Temporary URL, отсутствие Send и result, недоступность prompt, невыдачу preflight, отсутствие принудительного закрытия, отсутствие повторного claim при следующем alarm. **Тест не доказывает**, что ChatGPT UI реально включил Temporary Chat — это отдельный gate.

## Целевая интеграция — не делать однострочный патч

```text
Relay submit/claim (legacy v1 unchanged)
     │
     ├─ legacy extension v1: /#hermes-relay=... -> /c/... -> saved URL receipt
     │
     └─ explicit temporary extension v2:
          session_id + request_id + profile_nonce + tab_id + document_epoch
             -> open exact ?temporary-chat=true
             -> trusted visible UI Temporary attestation
             -> durable arm request / exactly-one Send
             -> match user turn + assistant after it + request_id
             -> opaque completion receipt (not /c/ URL)
             -> Relay ACK, Telegram delivery separately
             -> keep tab until explicit owner release / no silent close
```

Нужно перепроектировать все проверяющие участки, а не только URL:
1. **Relay:** добавить отдельно названный transport protocol v2, сохранить v1 неизменным; transactional request ID / attempt lock, shared canonical noReplay log (не два независимых источника истины).
2. **Background:** trusted UI proof по *текущему* `documentId`, profile session, route epoch и TTL, не по URL; атомарная выдача exactly-once Send authorization.
3. **Content:** не предполагать `/c/` route, не интерпретировать «пять секунд после Send» как завершённый ответ; match `request_id` в JSON-ответе, не только последний assistant bubble.
4. **Lifecycle:** browser owner separate, отсоединение Playwright/CDP не вызывает `context.close` owner-owned browser; tab remove только после явной release. Никаких автоперезапусков пропавшей Temporary Chat с повтором Send.
5. **CPU:** DOM MutationObserver с редким reconcile scan — отдельная экспериментальная версия после профилирования настоящего `hermes_relay_content.js`; сначала CPU baseline на текущем установочном SHA.
6. **Е2Е:** существующая установка пользователя только после SHA-CAS, резервной копии, windows native fixture, подтверждённого Temporary UI, живой цепочки и owner approval.

## Процессный эксперимент

Новый диагностический скрипт `Hermes-WebGPT/Watch-Hermes-Edge-Lifetime.ps1` (read-only) запускается в отдельном интерактивном PowerShell до воспроизведения:

```powershell
powershell -NoProfile -File .\Hermes-WebGPT\Start-Hermes-Edge-Temporary.ps1
powershell -NoProfile -File .\Hermes-WebGPT\Watch-Hermes-Edge-Lifetime.ps1 -Seconds 120 -EverySeconds 2
```

Он выводит **только PID/PPID, время и переходы видимости процесса**. Скрипт не читает вкладки, токены, тексты чатов, command line и не завершает процессы. Это не замер CPU и не полноценная диагностика причины завершения: для различения закрытия вкладки, браузерного crash и завершения родительским job нужны дополнительные события Windows и расширения. Если работающий браузер использует другой профиль/путь, отчёт может показать `not_yet_seen`.

## Не снижать безопасность для скорости

Microsoft рекомендует измерять виновный процесс через Edge Browser Task Manager (Shift+Esc), отключать лишние расширения, использовать Sleeping Tabs избирательно и проверять аппаратное ускорение. Не выключать sandbox/GPU/isolation и не включать sleeping/discard для активно генерирующих Temporary вкладок. Chrome MV3 service worker может завершаться спустя 30 секунд простоя — хранить прочные состояния в storage/Relay, не в глобальных JS-переменных.

## Ограничение по разрешениям и правилам сервиса

Автоматизированная браузерная отправка/извлечение ответов должны быть отдельно сверены с применимыми условиями OpenAI; браузерный ChatGPT не является заменой официального API. В этом стенде нет ни сетевого запроса к ChatGPT, ни попытки извлечения ответов.

См. общую SDD: `Hermes-WebGPT/docs/EDGE_TEMPORARY_CHAT_ARCHITECTURE_20261122.md`.
