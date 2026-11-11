# Implementation Plan — спецификация 004

## Текущий контекст

Код: Python stdlib, существующие Hermes browser_tool, штатный Gateway bootstrap,
локальная SQLite Relay и read-only монитор. Пакеты не устанавливаются.
Выбранные владельцы: `hermes_windows_browser`, `model_news_prefetch`,
`monitoring.queue_monitor`; архитектура и откат описаны в OpenSpec design.md.

## Порядок работы и результат

1. Сверить действующий project scope, код и параллельные изменения — выполнено.
2. Перепроверить текущие helper/cache/bootstrap с production imports — выполнено.
3. Исправить потерю кодов подтверждения сообщения в `other` — выполнено:
   каталог v3 и корректная миграция v1/v2, без подавления новых отказов.
4. Провести candidate-тесты, сохранить native-byte backup, применить SHA/CAS
   и повторить проверки установленного кода — выполнено.
5. Повторить OpenSpec strict / SDD crosswalk после изменения документов.
6. Полную FR-003 приёмку выполнить отдельно: свежие PID/loaded SHA, собственная
   безопасная browser-навигация и подтверждение Telegram. Текущий busy Gateway
   ради этой проверки не прерывать; неизвестные Send не повторять.

## Проверки по требованиям

FR-001–002: native launcher, PE/package negatives, пользовательские аргументы,
существующий Edge и явный выбор браузера. FR-003: unit-контракт receipt проверен,
свежая полная живая активация остаётся открытой. FR-004–006: TTL/capacity/copies,
промах без fetch, новая задача и неизменность canonical tool messages.
FR-007–009: приватность и версии каталога, новые ошибки vs уточнения, создание
latency target, окно исходов и отсутствие ложного Telegram delivery.

## Свежая проверка

Windows job `2a0195ff9fd94316979f8789b427cc2b`, rc 0: 114 проверок,
113 PASS / 1 SKIP (OS does not allow symlinks). Модули загружены из текущих
production-путей; модельных запросов и перезапусков 0. Диагностика живой очереди
job `4e79bee3b82048a8aab067b96045fd1f`, rc 1 — ожидаемый ATTENTION, не PASS:
новые/неподтверждённые ошибки сохраняются. Детали и SHA — в
`docs/RESPONSE_QUALITY_COMPLETION_20261111.md`.

## Приёмка

Отмечать реализацию требования только по приложенному доказательству.
OpenSpec strict и crosswalk не заменяют E2E и независимую приёмку релиза.
Не менять задачи подключения плагинов, concurrency или DOM-отправки других
участков проекта. Отдельные host/reviewer approvals не отменяются автоаппрувом SDD.
