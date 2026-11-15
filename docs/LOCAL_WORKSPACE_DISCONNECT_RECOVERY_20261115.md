# Hermes — восстановление Local Workspace ↔ Tunnel без потери запросов

Команда для Windows PowerShell из корня клона Podtverzhdeno/Tunnel:

```powershell
py -3 scripts\local_workspace_disconnect_doctor.py --check-dns
```

Проверка **только читает** четыре локальных HTTP-endpoint: 8765/healthz, 8080/readyz, 8080/health/mcp и 8080/health/control-plane. Опциональный --check-dns только резолвит api.openai.com и не отправляет HTTP-запросы. Ответы фильтруются по фиксированным полям, токены/учётные данные/тексты сообщений не выводятся. Браузер, Telegram, Gateway и Tunnel не запускаются и не перезапускаются.

| diagnosis | Значение | Действие |
|---|---|---|
| LOCAL_MCP_UNAVAILABLE | Local Workspace на 8765 не подтверждён | Проверить существующий MCP-процесс; запускать только один штатный экземпляр |
| TUNNEL_CLIENT_UNAVAILABLE | MCP отвечает, Tunnel на 8080 не подтверждён | Проверить существующий tunnel-client и его штатный doctor; не запускать дубликат |
| TUNNEL_CONTROL_PLANE_DEGRADED | Локальные сервисы живы, но связь control-plane деградировала | Проверить сеть, Wi-Fi/VPN, DNS; не трогать Gateway |
| CONTROL_PLANE_STATUS_UNAVAILABLE | Control-plane не подтвердил статус | Проверить tunnel-client doctor/status |
| LOOPBACK_SERVICES_RESPONDING_REMOTE_SESSION_UNVERIFIED | Локальные службы ответили | Проверить подключение ChatGPT и вызвать list_workspaces |

Даже работающие локальные health-checks **не доказывают** подключение к облачной сессии ChatGPT. Настоящая приёмка — успешный вызов инструмента через текущий чат.

Нельзя удалять журнал noReplay, копировать OTP/2FA/ключи, запускать несколько pollers, повторять неопределённые отправки и объявлять Telegram E2E завершённым по ответу локального сервиса.

Код: [local_workspace_disconnect_doctor.py](../scripts/local_workspace_disconnect_doctor.py). Тесты: [test_local_workspace_disconnect_doctor.py](../scripts/test_local_workspace_disconnect_doctor.py). 12 офлайн-тестов PASS; на реальной Windows подключение ещё не проверено.