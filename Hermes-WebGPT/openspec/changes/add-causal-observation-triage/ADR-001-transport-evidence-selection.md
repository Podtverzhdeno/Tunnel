# ADR-001 — выбор GPT-транспорта и причинной диагностики Hermes

Статус: предложенное решение, не production activation. Дата: 2026-11-22. Base: Tunnel main 350ea33080bc35245aa6741f85fa926cc8a8fcaf.

## Контекст и разграничение ownership

Цель — десятки независимых агентов с достоверным объяснением каждого шага, остановки, внешнего эффекта, доставки и исправления. GitHub Tunnel PR #12 уже является владельцем альтернативных transport-исследований; PR #8 исследует MCP/CDP; #10 и #11 меняют жизненный цикл Edge; #13 (текущая ветка) отвечает лишь за новую read-only диагностику. Hermes fork PR #1 владеет durable ledger/approval/fencing; PR #9 — изолированным событийным observer. Не дублировать dispatcher, browser sender или privilege broker.

## Критерии оценки

Экспертная, а НЕ измеренная шкала 1–5. Вес: законность/документированная поддержка 30%; идентификация полного ответа 25%; восстановление 20%; простота эксплуатации 15%; совместимость Hermes 10%.

| Вариант | Поддержка | Полнота событий | Recovery | Эксплуатация | Совместимость | Взвешенно из 5 | Решение |
|---|---:|---:|---:|---:|---:|---:|---|
| Обычный официально оплачиваемый Responses API | 5 | 5 | 4 | 4 | 3 | 4.45 | Допустим только по отдельному согласованию оплаты |
| Официальный SIWC OSS → Responses API | 5 | 5 | 4 | 3 | 3 | 4.30 | Предпочтительный кандидат для consented ChatGPT plan integration |
| Официальный Codex App Server | 5 | 5 | 4 | 2 | 2 | 4.05 | Параллельный кандидат, отдельный JSON-RPC adapter |
| Локальный Ollama/vLLM | 5 | 4 | 4 | 2 | 3 | 3.90 | Альтернатива, модель/GPU/качество измерить отдельно |
| Hermes legacy Codex private endpoint | 1 | 4 | 3 | 4 | 4 | 2.90 | Только read-only аудит, не трактовать как SIWC |
| Web Companion через Chrome/Edge UI | 2 | 2 | 2 | 2 | 5 | 2.30 | Сохранять установленную версию, не превращать DOM в durable API |
| Прямой Playwright/CDP вместо расширения | 2 | 2 | 2 | 2 | 3 | 2.10 | Не устраняет ограничения веб-интерфейса; research only |

Рейтинг не означает приказ автоматически переключить провайдер: обычный API-key имеет отдельно оплачиваемую стоимость, SIWC требует явного пользовательского OAuth scope, а Codex App Server — иной набор механизмов и approvals. Нет согласия => auth_blocked; без скрытого fallback на платный провайдер.

## Принятые решения

- Сохранить текущий production Relay и browser source неизменными; проверить SHA реально загруженной Windows сборки прежде чем выкладывать новый код. main расширения содержит manifest 0.6.6, а документация указывает локально установленный 0.9.7. Не подменять.
- Новый диагностический модуль PR #13 лишь обрабатывает очищенный отчет queue_monitor и помечает неопределенные состояния, не исполняет инструменты и не повторяет Send.
- Новый будущий transport выбрать только перед началом нового turn, атомарно привязав route, task/request/turn IDs, account permission, budget и exact effect reservation. Если Send или внешний write мог произойти, при неизвестном ACK не переключать маршрут и не отправлять повторно.
- Не переносить из старого Hermes Codex endpoint chatgpt.com/backend-api/codex в новый SIWC; официально задокументированный OSS маршрут — публичный https://api.openai.com/v1/responses и предоставленный scope chatgpt.tokens.use.direct.
- Для SIWC preview проверить store=false, stream=true, полное input на каждый turn, отсутствие неподдерживаемых previous_response_id/background/hosted tools. Delta не означает завершение; учитывать response.completed, failed, incomplete и 429. Пользовательское согласие и лимиты нельзя обходить.
- Для Codex App Server независимо привязать threadId/turnId/requestId/topic, обрабатывать interrupted/failed, item tool approvals и crash/restart. Нельзя давать модели самому одобрять действия. Codex App Server/CLI не предоставляет автоматически историю обычного ChatGPT Web и не гарантирует identical High model.
- Для browser extension MV3 worker suspension и CDP onDetach не доказывают остановку модели, а пустая очередь не доказывает доставку Telegram. Подтверждать Send intent, saved turn, exact assistant result, Gateway send receipt и Telegram ACK раздельно.
- Десятки агентов должны получать отдельные producer_id, lease/fence, worktree и scoped permission. Не допускаются общие незащищенные счетчики, last-writer-wins SDD и расширение прав через запись лог-события.

## Что именно нужно протестировать до включения

G0: идентифицировать real Windows loaded source SHA/PID/version Relay, Hermes, Edge и extension (Local Workspace MCP пока Session terminated).
G1: добиться официального owner-consented supported auth и реальных неопасных двух model turns; проверить независимое соответствие request/response ID, limited scope и budget.
G2: однократный запрос с tool followup, правильной темой Telegram и реальным delivery ACK (не synthetic mock).
G3: crash-before-intent, crash-after-intent/before-click, crash-after-send, lost ACK, old worker after new fence, rate limit, account revocation, source change, browser tab close. Нельзя дублировать uncertain external effects.
G4: отдельно измерить p50/p95 latency и success при 1, 5 и 25 логических agents. Никаких обещаний параллельного моделирования из числа вкладок.
G5: verified backup and rollback; независимый reviewer, privacy audit (никаких cookies/tokens/URL/chat text в событиях) и signed or independently anchored audit при production.
G6: повторить всё после реального merge с concurrent Edge/CDP ветками, а не полагаться на mergeable:true.

**NO-GO:** отсутствует право на транспорт, загрузочный SHA отличается от проверенного, нет проверки webhook/delivery, есть неисследованное uncertain действие, нет authenticated producer/tool gateway, или тесты ограничены synthetic fixture.

## Причинный анализ отказа

В каждом incident record требуются: observable symptom; source identity; exact task/request/action/target and fence; не менее двух возможных причин, где осмысленно; их разделяющий тест; уровень достоверности; минимальный fix; rollback и exact-SHA verifier. Только heartbeat не доказывает progress. Нет ответа не доказывает failure. Переоткрытие вкладки B не закрывает detach вкладки A. Перемена агента не обнуляет неопределенный эффект предыдущего fence. Частичный поток output не доказывает model turn complete.

## Источники и handoff

Canonical local source: Hermes-WebGPT/relay.py, monitoring/queue_monitor.py и существующий OpenSpec durable-web-turn-provenance. Research proposal: Tunnel PR #12 и Hermes-WebGPT/research/2026-11-22-alternative-transports.md. Параллельные браузерные PR #8/#10/#11. Зависимости: hermes-agent PR #1 durable ledger, PR #9 event observer.

Официальные интерфейсы: https://developers.openai.com/siwc/token-sharing-open-source ; https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations ; https://developers.openai.com/codex/app-server ; https://openai.com/policies/terms-of-use/ .
Lifecycle/telemetry: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/ ; https://developer.chrome.com/docs/extensions/reference/api/debugger/ ; https://www.sqlite.org/wal.html ; https://www.w3.org/TR/trace-context/ ; https://opentelemetry.io/docs/specs/semconv/gen-ai/ .

Эти оценки помогают выбрать проверяемые варианты, но не заменяют результаты live Windows, официальный OAuth consent, независимые receipts и E2E Telegram.