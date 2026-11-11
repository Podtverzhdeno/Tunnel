# Proposal — Hermes: готовность браузерного инструмента, сохранение источников между ходами и наблюдение задержек

## Why
На Windows Hermes выбирал POSIX npm shim вместо установленного native exe (WinError 193), затем не находил Chrome при наличии Edge. Публичные RSS-доказательства исчезали после первого tool turn, провоцируя лишний поиск. Существующий queue_monitor скрывает конкретные новые ошибки как other и не отделяет целевую скорость от утверждения о зависании.

## What Changes
- Проверенный браузерный launcher без shell и загрузки пакетов; доступные публичные источники сохраняются на ограниченное время после инструментов без повторного fetch; существующий read-only монитор выдаёт точные безопасные причины и превышение целевого времени, не заявляя Telegram delivery по Relay done.

## Capabilities

### New Capabilities
- **windows-browser-tool-readiness**: Проверяемая готовность именно браузерного инструмента Hermes, отдельно от транспорта ChatGPT.
- **followup-source-continuity**: Ограниченный cache уже найденных публичных доказательств для следующих model-only ходов.
- **response-quality-observation**: Доработка существующего queue_monitor вместо второго сервиса: типы ошибок и явные временные цели.

### Modified Capabilities
None identified in the submitted brief.

## Impact
Affected components and exact files: TBD during design review.

## Acceptance
Each SHALL requirement must pass the linked WHEN/THEN scenario.
