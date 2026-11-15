# A4 — выявление и восстановление расхождения загруженного Relay, 14.11.2026

## Факты: инцидент и восстановление

1. Read-only `loaded_runtime_parity.py` сравнил 4 зафиксированных самим Relay при импорте SHA256 с файлами в checkout. На работающем Windows-хосте обнаружен `DISK_CHANGED_AFTER_RELAY_START`: отличается **только `relay.py`** (job `a0a75f2413e94c37a5e9d04161f38531`, rc0). Это реальный source/runtime drift, а не «тест готовности».
2. `relay_reload_preflight.py` (использует существующий guard) первоначально подтвердил ownership/idle: `READY_FOR_EXPLICIT_RELOAD`, job `3f24aa6aeee5462693542c960a2e7a39`, rc0. В этот момент **ничего не выключалось**.
3. Перед любым применением проверка `guarded_relay_reload.py` отказала: `BLOCKED`, `reload_attempted=false`, job `4b2023d176824d0487a2fcf7fa1fa02c`, rc2. Следующее независимое наблюдение обнаружило исчезновение **и Relay, и Gateway**, при продолжающих работу MCP/Tunnel. Причина остановки в текущей проверке **не установлена**, нельзя утверждать, что их остановил наш скрипт: `--apply` в нём **не выполнялся**.
4. Read-only `quickstart.services relay` и `gateway` вернули `action=start`, на порту 18765 не было неизвестного процесса, действующих заданий Relay не было: состояния очереди `blocked=135`, `done=346`, `uncertain=38`. Неопределённые задания не были поставлены на повтор (jobs `5b1361a342434f35b7b17d33a1d0f65f`, `1b726d16974844e3aa39593ce03f32a6`, rc0).
5. **Реально восстановлены только остановленные службы:** `python -m quickstart.services relay --apply` (job `440811c5988b4db0a9aa1725174ffbff`, rc0, Relay HTTP healthy), затем `python -m quickstart.services gateway --apply` (job `369f1bfc1b5e4c679ea1d6ffd83ddfc2`, rc0, process PID attested).
6. После: `loaded_runtime_parity.py` вернул **MATCH по всем четырём файлам**, job `00b64c53d80747e6a93db7bf702d6568`; `dev_status.py` показал все 4 локальных компонента ready и **3 активных слота Relay**, job `6c5d11a0e3ea49ea963302a036c1509e`; Web Companion проверен с авторизацией.
7. Повторный настоящий `Start-Hermes-All.cmd` с встроенным предупреждением о версиях: **PASS**, `relay_loaded_build_match=true`, `web_companion_bridge_authenticated=true`, PID/receipts без изменения, AutoPilot Send по-прежнему неподтверждён. Job `462982c36a344060bd540194b4a9d4b6`, rc0.

## Код

- `quickstart/a4/loaded_runtime_parity.py`: однократная read-only сверка загруженного runtime и рабочей копии, без выдачи каких-либо SHA в stdout, без рестарта.
- `quickstart/a4/relay_reload_preflight.py`: повторно использует исходный Relay parent-child guard, проверяет idle и ownership до любых изменений.
- `quickstart/a4/guarded_relay_reload.py`: опциональное применение **только через `--apply`**, с сохранённым fail-closed поведением. В этом цикле apply данного скрипта **не применялся**: при drift хост уже был в состоянии BLOCKED. Рабочее восстановление проведено через текущие стабильные `quickstart.services` после проверки остановленных процессов.
- `quickstart/a4/dev_status.py` и мастер `setup.ps1` показывают `relay_loaded_build_status`. При старом runtime появляется предупреждение, но Hermes DEV не блокируется.
- `quickstart/start.ps1` дополнен read-only `DEV RELAY_BUILD_MATCH` / `DEV RELAY_BUILD_STALE` / `DEV RELAY_BUILD_UNKNOWN`, никаких автоматических перезапусков.
- Unit tests parity: 7 total / 6 PASS / 1 SKIP (symlink privilege), job `8a421b44b8134b50beaa029c6fb3d3e4`; guarded reload decision tests: 4/4 PASS, job `655a0b901a8c4843b2083fb67d25b186`.
- Финальная Windows Quickstart после добавления guarded-decision tests и предупреждения в launcher: **245 total, 234 PASS / 11 SKIP / 0 FAIL**, job `351414809dbc4dd286658ea0ab60252e`, rc0.
- После исправления обработки ошибок безопасный read-only запуск `guarded_relay_reload.py` вернул `NOT_REQUIRED`, rc0; восстановленный Relay не перезапускался. Job `61d469f2e2bf46b896585f3ba93e1d2a`; 4/4 unit tests PASS job `cbe1adb3040b4d15a62e55d0a8824c8e`.

## Что осталось

**Продуктовый блокер A1/A3:** `SEND_INTENT` всё ещё не подключён к авторизованному внешнему Web Companion handoff; реальный ChatGPT reply и Telegram delivery не верифицированы. Новый offline Runtime ZIP `v0.0.1-candidate` протестирован синтетическими тремя потоками, но не означает рабочие браузерные потоки.

**Восстановление не равно установке с нуля:** для production нужны чистый Windows checkout, полный комплект MCP/Tunnel/Web Companion, browser extension и независимая A5 приёмка.

**Отдельное расследование:** выявить источник внезапного прекращения Relay и Gateway по защищённым метаданным процессов/сервисов; без доказательства не назначать виновника.
