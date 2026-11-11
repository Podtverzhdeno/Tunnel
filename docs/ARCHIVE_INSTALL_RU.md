# Установка Tunnel из ZIP-архива

> **Обновление от 11.11.2026:** для переноса на другой ПК используйте **[Tunnel-Portable.zip](../dist/Tunnel-Portable.zip)** и новую **[инструкцию по подключению туннеля](TRANSFER_TO_NEW_PC_RU.md)**. Ниже сохранено руководство для архивной сборки от 4 ноября; оно может отличаться от актуального набора. ZIP без ключей не отменяет ограничений аккаунта ChatGPT.

Это переносимый комплект **исходников**, а не архив чужого аккаунта ChatGPT. В нём нет API-ключей, сессионных токенов, cookies, Tunnel ID или авторизованных приложений. У нового пользователя они должны быть **свои**.

## Какой архив выбрать

| ZIP | Содержимое | Назначение |
|---|---|---|
| `Tunnel-Complete-Windows.zip` | Local Workspace MCP, New Chat Smoke Test, Web Companion, браузерное расширение, Chain2/3, P085 research, README и installer | **Рекомендуется**: всё в одном пакете |
| `Local-Workspace-MCP.zip` | Исходники Local Workspace MCP, скрипт авторизации и тесты | Для самостоятельной интеграции MCP |
| `New-Chat-Smoke-Test.zip` | `plugin.json`, `mcp.json`, `server.py`, `panel.html`, тестовая утилита | Исходный код отдельного New Chat Smoke Test |
| `Web-Companion-Extension.zip` | Расширение Chromium + локальный bridge и контроллеры Chain2/3 | Для самостоятельной установки браузерного компонента |

Все архивы снабжены SHA-256 в `dist/SHA256SUMS.txt`. Для первого запуска используйте **полный архив** — отдельные части нужны скорее разработчикам.

## Полный архив: установка на новом Windows-компьютере

1. Скачайте `Tunnel-Complete-Windows.zip` из [репозитория Tunnel](https://github.com/Podtverzhdeno/Tunnel/tree/main/dist) и распакуйте в папку без пробелов и спецсимволов, например `C:\Work\Tunnel`. Внутри архива уже есть верхнеуровневая папка `Tunnel`.
2. Установите [Python 3.10+](https://www.python.org/downloads/windows/) и при необходимости Git. Откройте PowerShell в **папке `Tunnel`** (где находится `INSTALL_FROM_ARCHIVE.ps1`).
3. Проверьте содержимое скрипта и запустите:

```powershell
py -3 --version
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1
```

По умолчанию создаётся `%USERPROFILE%\AgentProjects` (read-only alias `projects`). Если нужен другой каталог:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1 -ProjectsPath "D:\MyProjects"
```

Опция `-AllowWrite` разрешает операции записи/выполнения **в пределах указанной папки** локальному исполнителю; для первого теста её лучше не использовать. Скрипт **не требует прав администратора**, не создаёт Windows-службу и не запускает браузер сам.

4. Запустите Local Workspace MCP из корня распакованной папки:

```powershell
py -3 scripts\observer_auth.py serve --port 8765
```

Оставьте это окно открытым. Скрипт сам загрузит **отдельный локальный bearer** для сервера, порт `127.0.0.1:8765`.

5. Откройте второе окно PowerShell в корне `Tunnel` и запустите браузерный bridge:

```powershell
cd chat-launch-smoke-desktop-test\web-companion
py -3 start_bridge.py
```

6. В Chrome, Chromium или Яндекс Браузере откройте `chrome://extensions` или `browser://extensions`, включите режим разработчика → **«Загрузить распакованное»** → выберите **`Tunnel\chat-launch-smoke-desktop-test\web-extension`**. Токен расширения уже создан установщиком в приватном конфиге; вручную вводить его не нужно.

7. Создайте собственный **Secure MCP Tunnel** и приложение **Local Workspace** в ChatGPT. Для этого пройдите [раздел 4–7 полного README](../README.md). Это операция, требующая личного аккаунта и разрешённой платформой функции custom MCP. Архив **не может сам создать** приложение, API-ключ или Tunnel ID в чужом ChatGPT.

8. Из корня `Tunnel` проверьте:

```powershell
py -3 scripts\status-stack.py
py -3 scripts\chain2-triage.py
```

## Где взять и куда вставить API-ключ / токены

**OpenAI API key**: [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Нужен **только** для отдельного API-исполнителя (которого данная установка не создаёт). В PowerShell его можно установить как `$env:OPENAI_API_KEY = Read-Host "OpenAI API key"`. Для веб-ChatGPT и расширения этот ключ не нужен.

**Tunnel Runtime API key**: OpenAI Platform → Organization API keys с правами **Tunnels Read + Use**. В том же окне PowerShell, где запускается `tunnel-client`:

```powershell
$env:CONTROL_PLANE_API_KEY = Read-Host "Tunnel runtime API key"
$tunnelId = Read-Host "Tunnel ID (tunnel_...)"
```

**Tunnel ID**: создаётся в OpenAI Platform → Tunnels и передаётся как `--tunnel-id $tunnelId` при настройке `tunnel-client`. Для HTTP Local Workspace используйте профиль `sample_mcp_remote_no_auth` с `--mcp-server-url http://127.0.0.1:8765/mcp`.

**Локальный HTTP bearer MCP**: установщик автоматически создаёт `Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\observer-authorization-header`. В PowerShell для `tunnel-client` укажите **путь** к файлу:

```powershell
$headerFile = (Resolve-Path ".\Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\observer-authorization-header").Path
$env:MCP_EXTRA_HEADERS = "Authorization: file:$headerFile"
$env:MCP_DISCOVERY_EXTRA_HEADERS = "Authorization: file:$headerFile"
```

**Токен браузерного расширения**: создаётся через `setup_bridge.py`, затем `build_clean_extension.py` создаёт `web-extension/bridge-config.json`. Установщик делает оба шага за пользователя.

**Нельзя загружать** в GitHub или в чат `.env`, `bridge-config.json`, `bridge-state/`, `.local-mcp-state/`, ключи, cookies и токены сессии.

## Можно ли «просто загрузить ZIP в ChatGPT»?

**Нет гарантии автоматического импорта через ZIP.** ChatGPT custom MCP настраивается через интерфейс Plugins с адресом/туннелем сервера, а не переносом чужого App ID. В зависимости от доступных функций у пользователя может быть иной процесс установки MCP Apps. Архивы `New-Chat-Smoke-Test.zip` и `Local-Workspace-MCP.zip` предоставляют **полные исходники**, которые можно разворачивать на своём сервере/ПК, но не представляют собой OAuth-авторизацию или заранее зарегистрированное приложение.

## Проверка целостности архива

```powershell
Get-FileHash .\Tunnel-Complete-Windows.zip -Algorithm SHA256
```

Сравните хеш с `SHA256SUMS.txt` в каталоге GitHub `dist` (не с текстом внутри самого ZIP). Для новых версий значения будут другими.

**Ограничение:** Chain3 `trusted` остаётся заблокированной без реального независимого OS-protected P085, а Chain2 требует приёмки исходных задач. ZIP не отменяет это ограничение. Для первого теста используйте sandbox и read-only диагностику.
