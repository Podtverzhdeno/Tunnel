# A4 DEV: Web Companion guarded autostart + Yandex handoff — 14.11.2026

## Что теперь делает обычный запуск Hermes

1. `Start-Hermes-All.cmd` запускает текущий DEV `quickstart/start.ps1` (производственная A4-проверка не блокирует разработку).
2. После MCP/Tunnel/Relay/Gateway в DEV выполняется **существующий A1-guard** `Hermes-AutoPilot-Deploy-20261113/guarded_start_companion_bridge_v2.py`. Он проверяет SHA исходника Bridge, исходный PID, владельца сокета, авторизованный HTTP 200 и отсутствие опасного состояния очередей до любого запуска. A4 не создаёт новую очередь и не копирует ключи.
3. Если Bridge подтверждён как `ALREADY_RUNNING_VERIFIED` или `STARTED_VERIFIED`, в консоль выводится `WEB_COMPANION_BRIDGE_AUTH_OK`. При BLOCKED/UNKNOWN выдаётся предупреждение, не утверждается готовность. В DEV при отсутствии отдельного A1-проекта запуск остальных компонентов не блокируется.
4. После этого read-only `yandex_handoff_readiness.py` проверяет регистрацию AutoPilot и локальный манифест из доверенного каталога. Выводит `AUTOPILOT_SEND_UNVERIFIED`, пока нет настоящего доказательства доставки.
5. Dry-run и STRICT не запускают A1-Bridge guard. Никаких сообщений в ChatGPT или Telegram эти проверки не отправляют.

## Результат на реальном Windows ПК

- Прямой запуск существующего A1 Bridge guard: `ALREADY_RUNNING_VERIFIED`, `200_AUTHENTICATED`, rc0; job `ca3e8f034ff14b0f90e6e28ed298ea4b`.
- Реальный двойной клик через `Start-Hermes-All.cmd`: **PASS**, exit 0, `web_companion_bridge_authenticated=true`, `autopilot_send_unverified_warning=true`, идентификаторы Relay/Gateway и receipt неизменны; отправок и загрузок тестом **0**. Job `41f096dd9baf47e295cdd7d0187f4b9c`.
- Focused `test_dev_companion_bootstrap.py`: **3/3 PASS**, job `1f0659448da04a2f89a93610ac481b4f`.
- Full Windows Quickstart: **222 total, 213 PASS / 9 SKIP / 0 FAIL**, job `8f83c8b907e6433dbfcc0e39a0f0fc03`.
- A4 read-only Yandex source check: `source_versions_observed=[0.8.1]`, registered manifest metadata incomplete, no declared content script, source↔live-worker not verified, Bridge unauthenticated probe says AUTH_REQUIRED. Authenticated Bridge guard passes independently. Read-only job `6e11980a96fa442e8540c598ccdfc369`.

## Чего не закрывает

- **Нет доверенного внешнего A1 ↔ A3/Web Companion handoff.** A2 crosswalk фиксирует `SEND_INTENT → TRUSTED_WEB_COMPANION_NOT_CONNECTED`; A1 internal API не является внешним удостоверенным каналом.
- Нет зафиксированного ChatGPT Send / ответ Verified / Telegram Delivered; нет трёх параллельных реальных потоков; файлы и изображения не приняты.
- Этот DEV autostart требует соседний A1 checkout, не является независимой установкой из чистого клона.
- SOURCE_OBSERVED ≠ BROWSER_LOADED: v0.8.1 прочитана из локального файла регистрации, но активная загрузка service worker этим не доказана.
- Не переводить PR в Ready/merged и не утверждать 100% завершения.

## Следующий приоритет

A1/A3: добавить удостоверенный сеансовый интерфейс с одним владельцем отправки, no-replay lease, подписанной привязкой run/worker/tab/conversation и подтверждением MESSAGE_SAVED. Затем один безопасный E2E и три параллельные задачи. A4 после этого проверяет автозапуск расширения, привязку релиза и чистую установку.
