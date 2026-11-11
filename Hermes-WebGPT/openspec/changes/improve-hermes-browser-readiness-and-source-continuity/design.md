# Design — Hermes: готовность браузерного инструмента, сохранение источников между ходами и наблюдение задержек

## Context
На Windows Hermes выбирал POSIX npm shim вместо установленного native exe (WinError 193), затем не находил Chrome при наличии Edge. Публичные RSS-доказательства исчезали после первого tool turn, провоцируя лишний поиск. Существующий queue_monitor скрывает конкретные новые ошибки как other и не отделяет целевую скорость от утверждения о зависании.

## Goals
Проверенный браузерный launcher без shell и загрузки пакетов; доступные публичные источники сохраняются на ограниченное время после инструментов без повторного fetch; существующий read-only монитор выдаёт точные безопасные причины и превышение целевого времени, не заявляя Telegram delivery по Relay done.

## Architecture
TBD: identify existing entrypoints, trust boundaries, data flows and component responsibilities before implementation.

## Constraints
- Расширять существующие Hermes, Web Companion и queue_monitor; не создавать второй транспорт, Gateway или ledger вместо уже планируемого stabilize-hermes-tool-fallback-and-evidence.
- Сохранять исходные system/developer/user/tool сообщения, tool_choice, схемы и запрет повтора неизвестного Send.
- Не устанавливать браузеры или плагины автоматически; использовать существующий native agent-browser и изолированный профиль существующего браузера.
- Уважать явный AGENT_BROWSER_EXECUTABLE_PATH и пользовательскую вкладку/профиль; никакого shell=True и подстановок аргументов.
- Каноническое создание OpenSpec/SDD выполняется штатным брокером после свежего preview и prepare: по точному foreground owner approval либо по активной политике автоаппрува, явно включённой владельцем для Hermes-WebGPT. Сохраняются SHA плана, срок запроса, no-overwrite и strict validation.

## Decisions & alternatives
- Proposed design: TBD; compare at least one alternative and existing behavior.
- Security: define read/write scope, rollback and untrusted inputs.
- Failure modes: timeouts, retries, stale data, privilege failures.

## Rollback
TBD: define a reversible strategy before approving state-changing implementation.
