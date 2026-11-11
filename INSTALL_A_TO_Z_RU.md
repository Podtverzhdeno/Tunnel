# Tunnel — установка Local Workspace MCP на Windows от А до Я

**Инструкция для нового компьютера и для переноса готовой системы.** Обновлено 11 ноября 2026 года. Репозиторий: [Podtverzhdeno/Tunnel](https://github.com/Podtverzhdeno/Tunnel).

> **Проверенная возможность:** ChatGPT через Secure MCP Tunnel читает файлы и папки, которые пользователь разрешил в Local Workspace MCP. Базовый профиль `observer` **не изменяет файлы**. Полноценная защищённая запись из ChatGPT и P085/Trusted пока **не подтверждены как готовый режим**. Настройка браузера, Chain2 и Chain3 для обычного чтения локальных файлов не требуется.

## Статус проверки и границы гарантий

По состоянию на 11.11.2026 исходники GitHub сверены с этой инструкцией: установщик `INSTALL_FROM_ARCHIVE.ps1`, `observer_auth.py`, `manage.py`, read-only allowlist из 6 инструментов и `/healthz` существуют. В [отчёте QA](docs/PORTABLE_QA_20261111.md) зафиксированы успешные офлайн-тесты пакета, Windows-установщика, HTTP bearer (200/401) и проверка ZIP. **Это не является подтверждением живого E2E-подключения с любого нового компьютера.**

Принцип приёмки: **исходники на GitHub → локальный `/healthz` и защита → статус runtime → настоящий вызов инструментов из ChatGPT**. Только последний этап доказывает работоспособность всей цепочки. Наличие 28 инструментов у другого экземпляра Local Workspace означает иной, более широкий профиль; для `observer` ожидаются именно 6 инструментов.

## 0. Как это работает

```text
ChatGPT → плагин Local_WorkSpace → OpenAI Secure MCP Tunnel
→ tunnel-client.exe на Windows → http://127.0.0.1:8765/mcp
→ разрешённые папки вашего ПК (по умолчанию только чтение)
```

Для работы нужны локальный Python MCP-сервер, клиент туннеля и подключение плагина в ChatGPT. **Browser Bridge** на `127.0.0.1:18763` — отдельный, необязательный компонент. **Проброс портов на роутере не нужен.**

### Что установить

| Компонент | Обязателен? | Для чего |
|---|---|---|
| Windows 10/11, PowerShell 5.1+ | Да | Запуск и диагностика |
| Python 3.10+ и `py` launcher | Да | Local Workspace MCP; проверен и на Python 3.12.10 |
| `Tunnel-Portable.zip` | Да | Исходники и установщик |
| Полный `tunnel-client.exe` для Windows | Да | Подключение локального MCP к OpenAI |
| Tunnel ID и **runtime** API key | Да | Авторизация OpenAI Tunnel |
| Право добавить custom MCP в ChatGPT | Да | Доступ к инструментам из чата |
| Node.js/npm | Нет | Только для дополнительных сценариев |
| Chromium-браузер и Web Companion | Нет | Только для опциональной браузерной интеграции |
| OpenAI model API key | Нет | Не требуется для уже описанного MCP-туннеля |

Сначала убедитесь, что в вашем аккаунте/рабочем пространстве доступна функция **ChatGPT → Settings → Plugins → Add custom MCP server**. Её доступность зависит от плана, организации и прав пользователя. Локально установленный сервер сам по себе не добавляет право создавать плагины.

Полезные ссылки: [OpenAI Tunnels](https://platform.openai.com/settings/organization/tunnels), [Runtime API keys](https://platform.openai.com/settings/organization/api-keys), [Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [подключение MCP в ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## 1. Скачайте правильный архив и проверьте его

Для новой установки используйте **[dist/Tunnel-Portable.zip](dist/Tunnel-Portable.zip)**. Сопутствующие файлы: [diagnostics.txt](dist/diagnostics.txt) и [Tunnel-Portable.sha256](dist/Tunnel-Portable.sha256). Архив содержит исходники без пользовательских токенов и сессий.

Для опубликованного пакета от 11.11.2026 проверочный SHA-256: `8dd9b79c8e889f6cdea8db4bec9bc315ee5f2a159b370367a1031549bfb7159b`. Это контрольная сумма **конкретной версии**, а не всех будущих релизов.

Скачайте ZIP в папку «Загрузки» и выполните:

```powershell
py -3 --version
Get-FileHash "$env:USERPROFILE\Downloads\Tunnel-Portable.zip" -Algorithm SHA256
```

**Сравните хеш с данными для этой же версии архива.** Если хеш отличается — не запускайте установку.

### Куда распаковать

**Не заменяйте существующий рабочий `Tunnel` новой копией.** Ниже используется единый пример установки в `$env:USERPROFILE\Tunnel-Install\Tunnel`. Если выбрали другое место, замените его во **всех новых окнах PowerShell**:

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\Tunnel-Portable.zip"
$dest = Join-Path $env:USERPROFILE "Tunnel-Install"
if (Test-Path $dest) { throw "Папка уже существует — выберите новую пустую папку" }
New-Item -ItemType Directory -Path $dest | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $dest
$repo = Join-Path $dest "Tunnel"
Set-Location $repo
Test-Path .\INSTALL_FROM_ARCHIVE.ps1
Test-Path .\scripts\observer_auth.py
```

Обе проверки должны вывести `True`. В архиве есть верхнеуровневая папка `Tunnel`: переходите **в неё**, чтобы рядом находились `INSTALL_FROM_ARCHIVE.ps1` и `scripts`.

Если у вас уже есть установленный проект, **не перезаписывайте его**. Используйте его фактический корневой каталог вместо `"$env:USERPROFILE\Tunnel-Install\Tunnel"`; повторная распаковка не обязательна.

> **PowerShell:** приглашение `PS C:\...>` вводить не надо. Если команда требует продолжения и появился `>>`, нажмите `Ctrl+C` и вставьте правильную команду целиком. Не оставляйте `|` в конце строки. Не запускайте относительные пути из `C:\WINDOWS\system32`.

## 2. Установите локальный MCP

В PowerShell **из корня проекта**:

```powershell
py -3 scripts\verify-portable-install.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1
```

Успешная проверка — JSON с `"ok": true`; успешная установка — **`Local setup complete`**. Скрипт подготавливает локальную конфигурацию, папку проектов, отдельные локальные секреты MCP и Web Companion. Он **не создаёт** туннель, плагин ChatGPT и Windows-службу.

Для назначения другой папки проектов:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1 -ProjectsPath "$env:USERPROFILE\AgentProjects"
```

**Не начинайте с `-AllowWrite`:** текущий скрипт при использовании этого параметра может выставить локальные разрешения `write`, `execute`, `git`. Профиль `observer` всё равно защищён от публикации операций записи, а корень с `write=true` может помешать запуску observer.

### Если повторный запуск установщика прерван

```text
Source extension contains private/extra files
Portable preflight did not pass.
```

Причина часто в `web-extension\bridge-config.json`, созданном предыдущей установкой. Проверка ожидает **чистый комплект исходников**, а не приватную рабочую конфигурацию. **Не используйте `-SkipPreflight` для обхода**, не удаляйте случайные файлы. Предпочтительно заново распаковать ZIP в **пустой каталог**, а рабочий `bridge-config.json` оставить в защищённой резервной копии вне нового дерева исходников.

## 3. Запустите сервер MCP

Откройте **первое окно PowerShell**, перейдите в корень установленного `Tunnel` и выполните:

```powershell
py -3 scripts\observer_auth.py check
py -3 scripts\observer_auth.py serve --port 8765
```

Если `check` сообщает об отсутствующем токене, сначала выполните `py -3 scripts\observer_auth.py init` и снова `check`.

Ожидаемое сообщение:

```text
Local Workspace MCP listening at http://127.0.0.1:8765/mcp
```

**Не закрывайте окно сервера.** В **другом** PowerShell проверьте:

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/healthz" | ConvertTo-Json
```

Пример корректного ответа:

```json
{"status":"ok","server":"local-workspace-mcp","mcp_url":"http://127.0.0.1:8765/mcp","tools":6,"workspaces":1}
```

- `tools: 6` — **ожидаемо** для read-only observer. Команда `py -3 manage.py doctor` может показывать **28** инструментов исходного сервера: это **не** значит, что они все удалённо разрешены.
- `workspaces` — число реально загруженных разрешённых папок.
- Не запускайте вторую копию MCP на уже занятом порту `8765`.

## 4. Добавьте нужные папки (read-only)

В новом PowerShell переходите в `Local-Workspace-Extensions\mcp-host-source` **внутри вашей установленной папки Tunnel**:

```powershell
cd "$env:USERPROFILE\Tunnel-Install\Tunnel\Local-Workspace-Extensions\mcp-host-source"
py -3 manage.py add-root --name tools --path "$env:USERPROFILE\Tools"
py -3 manage.py add-root --name shelter --path "$env:USERPROFILE\Documents\Obsidian Vault\MyVault"
py -3 manage.py list-roots
py -3 manage.py doctor
```

Пути с `Vadim` — **только пример**: замените на ваш профиль и каталог. Добавляемые папки уже должны существовать. Не добавляйте весь диск `C:\`, домашнюю папку или каталог браузерных секретов.

Если сервер пишет **`Workspace name/path already used`**, запись уже существует: `add-root` **не изменяет** ранее заданные права. Проверьте `list-roots`; не добавляйте ещё раз с `--write`.

**После добавления папки обязательно перезапустите HTTP MCP**: в окне с `observer_auth.py` нажмите `Ctrl+C`, затем из корня `Tunnel` снова:

```powershell
py -3 scripts\observer_auth.py serve --port 8765
```

В другом окне повторите `/healthz`. Если `manage.py list-roots` показывает 2 папки, а `/healthz` — 1, то на `8765` остался старый процесс или загружена другая конфигурация. Проверьте, кто занимает порт:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" | Select-Object ProcessId,Name,CommandLine }
```

Останавливайте через `Ctrl+C` в исходном окне; если оно утеряно, завершайте процесс **только после проверки имени, PID и строки запуска**, а не по догадке.

В самом MCP используйте **алиасы** (`tools`, `shelter`, `projects`), а не абсолютные пути. Пути в аргументах `list_directory` **относительны** выбранному алиасу.

## 5. Установите OpenAI Tunnel Client

Скачайте **полную** Windows-сборку из [официальных релизов](https://github.com/openai/tunnel-client/releases/latest): `windows-amd64` для Intel/AMD, `windows-arm64` для ARM. Не используйте только `tunnel-client-runtime`, если нужны команды управления. Проверьте SHA-256 по релизу. Распакуйте комплект (включая `cloudflared.exe`) в:

```text
%USERPROFILE%\Tools\TunnelClient
```

В **новом** PowerShell:

```powershell
$client = Get-ChildItem "$env:USERPROFILE\Tools\TunnelClient" -Recurse -File -Filter tunnel-client.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $client) { throw "Не найден tunnel-client.exe" }
& $client --version
& $client help quickstart
& $client runtimes list
```

Если вы открыли PowerShell прямо в папке с `.exe`, пишите **`.\tunnel-client.exe`**, а не `tunnel-client`. PowerShell не запускает исполняемый файл из текущей директории без `\.\`, если он не находится в PATH. Не копируйте `.exe` в `C:\WINDOWS\system32`.

## 6. Подключите существующий Secure MCP Tunnel

В [OpenAI Platform → Tunnels](https://platform.openai.com/settings/organization/tunnels) найдите **свой** `tunnel_...`. Создавать новый Tunnel ID **не нужно**, если нужный уже существует. Для процесса потребуется **runtime API key** с правами `Tunnels Read + Use` из [Organization API keys](https://platform.openai.com/settings/organization/api-keys). Не используйте ключ API модели и не отправляйте свои ключи в чат.

В **том же PowerShell**, где существует переменная `$client`, введите данные:

```powershell
$tunnelId = Read-Host "Existing Tunnel ID"
$key = Read-Host "Tunnel Runtime API key" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
try { $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); Remove-Variable key,ptr -ErrorAction SilentlyContinue }
```

**Важно:** текст `Existing Tunnel ID` в кавычках — **только приглашение ввода**. Введите `tunnel_...` после него, а не вместо текста приглашения. Ключ скрыт звёздочками и не должен попадать в Git, файлы проекта или чат.

Затем в **корне вашего Tunnel** подготовьте локальный Authorization из уже созданного приватного файла:

```powershell
cd "$env:USERPROFILE\Tunnel-Install\Tunnel"
$headerFile = (Resolve-Path ".\Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\observer-authorization-header").Path
$env:MCP_EXTRA_HEADERS = "Authorization: file:$headerFile"
$env:MCP_DISCOVERY_EXTRA_HEADERS = "Authorization: file:$headerFile"
```

**Замените `cd` на действительный корень вашей установки.** Содержимое файла **не показывайте** и не переносите на другие компьютеры.

Подключите MCP к **существующему** туннелю (проверенная форма CLI v0.0.20):

```powershell
& $client runtimes connect --alias local-workspace --tunnel-id $tunnelId --runtime-api-key env:CONTROL_PLANE_API_KEY --mcp-server-url "http://127.0.0.1:8765/mcp"
& $client runtimes status local-workspace --json
```

Возможный вывод: `Connected local-workspace using process mode`. **Не считайте его доказательством полной готовности без следующей команды.** Для успешного локального статуса проверьте:

```json
{"process_running":true,"healthy":true,"ready":true,"runtime_state":"ready"}
```

Это **поля внутри большого JSON**, а не полный JSON. Для диагностики:

```powershell
& $client doctor --profile local-workspace --profile-dir "$env:APPDATA\tunnel-client" --explain
```

`control_plane_poll_health: unknown` и сообщение о том, что переменная `OPENAI_ADMIN_KEY` не установлена, **сами по себе** не означают отказ. Для обычного запуска административный ключ не нужен. Ошибка `tools/call` (например, HTTP 400) при таком статусе всё же возможна: статус процесса и **выполнение MCP-вызовов** — две разные проверки.

Для длительно работающего runtime используйте **`runtimes connect`**, а не `nohup` или `disown`. После каждого переподключения делайте **`runtimes status ... --json`**. Если этот Tunnel ID ещё обслуживает старый ПК, организуйте контролируемое переключение и остановите старый runtime, чтобы запросы не попадали на разные машины.

## 7. Создайте или обновите плагин в ChatGPT

Пока **observer MCP и Tunnel Client работают**:

1. В ChatGPT откройте **Settings → Plugins** (названия пунктов могут отличаться в разных аккаунтах).
2. **Add custom MCP server** → подключение **Tunnel** → выберите нужный Tunnel ID.
3. Название, например, **Local_WorkSpace**; описание — **Локальные файлы через Secure MCP Tunnel**.
4. Для этого конкретного observer MCP, где bearer добавляет `tunnel-client`, схема в настройках ChatGPT — **«Не требуется»** (No authentication), **если интерфейс её предлагает**. Не выбирайте OAuth, если у локального сервера не настроен настоящий OAuth-провайдер.
5. Проверьте набор инструментов и вызовите **list_workspaces**, затем **list_directory** для разрешённой папки. При необходимости нажмите **«Обновить инструменты»**, откройте новый чат и попробуйте ещё раз.

У полноценного read-only Local Workspace MCP должны присутствовать:

- `list_workspaces` — разрешённые корни;
- `list_directory` — содержимое папки;
- `directory_tree` — дерево каталогов;
- `file_info` — размер/время/SHA-256;
- `read_file` — чтение текстового файла;
- `search_text` — поиск в текстовых файлах.

Если плагин показывает только **`server_info`, `echo`, `uppercase`**, это **демонстрационный MCP**, а не нужный файловый сервер. Проверяйте выбранный туннель и обновление инструментов. Надпись `DEVELOPMENT` или версия `1.0.0` сами по себе не означают неисправность.

Настройка разрешений **«Разрешать действия с низким риском»** влияет на запросы подтверждения в ChatGPT, **не** отменяет ограничения сервера.

## 8. Полная проверка установки

Не публикуйте в публичных отчётах значения ключей, токенов или полные диагностические логи без проверки на секреты.

В отдельном PowerShell **из корня Tunnel**:

```powershell
py -3 scripts\status-stack.py
Invoke-RestMethod "http://127.0.0.1:8765/healthz" | ConvertTo-Json
```

В окне с `$client`:

```powershell
& $client runtimes status local-workspace --json
```

И в чате ChatGPT — **два реальных вызова**: `list_workspaces` и `list_directory` по одному из найденных alias.

**Установка для чтения считается подтверждённой только если:** HTTP MCP отвечает, инструментов `6`, число workspaces совпадает, runtime имеет `process_running=true`, `healthy=true`, `ready=true`, а ChatGPT действительно может перечислить каталог.

`status-stack.py` может показывать `tunnel: not_checked` даже при работающем туннеле: его статус проверяется **отдельно** через `runtimes status`. `bridge: not_running`, `extension: stale` и `full_autonomous_ready: false` **не блокируют** обычное чтение файлов через MCP.

## 9. Браузерный Web Companion — необязательно

Для Web Companion откройте **ещё одно** окно PowerShell в корне Tunnel:

```powershell
cd .\chat-launch-smoke-desktop-test\web-companion
py -3 setup_bridge.py
py -3 build_clean_extension.py
py -3 start_bridge.py
```

Bridge обычно слушает **127.0.0.1:18763**. В Chromium/Chrome откройте `chrome://extensions` (Яндекс Браузер: `browser://extensions`), включите режим разработчика → **Загрузить распакованное**, укажите **`chat-launch-smoke-desktop-test\web-extension`**, а **не** `web-companion` или ZIP. Войдите в ChatGPT в браузере самостоятельно и проверьте расширение. Не переносите cookies, browser profile, localStorage и ключи со старого ПК.

**Версии различаются по источнику:** в корне публичного GitHub-дерева `chat-launch-smoke-desktop-test/web-extension/manifest.json` указана **0.6.6**; в проверенной базовой части `Tunnel-Portable.zip` — **0.8.5**; более свежий снимок **0.9.7** находится в `migration-snapshot/` архива и автоматически не устанавливается. Поэтому сравнивайте версию именно используемого каталога, а не только GitHub. Не смешивайте файлы разных версий без тестов.

## 10. Чтение против изменения: реальные ограничения

**Профиль `observer` — строго read-only.** Даже если локальный `manage.py` знает 28 инструментов или вы выполнили `add-root --write`, через безопасный MCP-туннель **не появятся** методы редактирования, переименования и удаления. Для существующего root повторное `add-root --write` выдаст `Workspace name/path already used`. Сам root с `write=true` в observer может завершиться ошибкой `OBSERVER_ROOT_MUST_BE_READ_ONLY`.

**Не включайте для удалённого ChatGPT `legacy` HTTP, не отключайте авторизацию и не правьте флаги обходными путями.** Для записи потребуется отдельный защищённый компонент с чёткими каталогами, проверкой личности, журналом операций, проверкой хешей, резервными копиями и подтверждением опасных действий. Экспериментальный P085/Trusted пока нельзя считать проверенным рабочим production-компонентом.

Для чтения Obsidian достаточно добавить конкретный Vault; **не требуется доступ ко всему диску**.

## 11. Диагностика распространённых ошибок

| Что увидели | Причина и действие |
|---|---|
| `can't open file C:\WINDOWS\system32\scripts\observer_auth.py` | Сначала `cd` в **корень Tunnel**, потом `py -3 scripts\observer_auth.py serve --port 8765` |
| `INSTALL_FROM_ARCHIVE.ps1 does not exist` | Запущено из чужой папки или распакована неверная вложенность; `Test-Path` должен вернуть `True` |
| `Source extension contains private/extra files` | Часто рабочий `bridge-config.json` после установки; **не отключайте preflight**, используйте чистый ZIP и резервную копию приватного файла |
| `PS C:\...>` вызвало `Cannot find a process` | Скопировали **приглашение**, а не команду |
| `>>` / `An empty pipe element is not allowed` | `Ctrl+C`, вставьте корректную целую строку без висящего `\|` |
| `tunnel-client not recognized` | Используйте `.\tunnel-client.exe`, полный путь или `& $client` |
| `Workspace name/path already used` | Alias уже добавлен, проверьте `manage.py list-roots` |
| `manage.py doctor` показывает 28, `/healthz` показывает 6 | **Нормально** для read-only observer |
| `manage.py list-roots` — 2, `/healthz` — 1 | Старый процесс/конфиг на `8765`: выяснить PID, аккуратно перезапустить observer |
| `tools/call` возвращает HTTP 400 | Локальный MCP получил, но отклонил вызов; проверьте инструмент, аргументы, авторизацию и alias |
| `OAuth discovery invalid metadata` | Статический bearer observer не является полноценным OAuth; проверьте совместимую схему подключения |
| Плагин видит только `echo`/`uppercase`/`server_info` | Подключено демо-приложение или старый каталог инструментов; проверьте сервер и обновите инструменты |
| `tunnel: not_checked` в status-stack | Запустите `runtimes status local-workspace --json` отдельно |
| `bridge: not_running` и расширение stale | Для чтения файлов не важно; устраняйте только при работе с браузерной интеграцией |
| `Chain3: blocked` | Ожидаемо для экспериментального режима без приёмки P085/Chain2; не обходите защиту |

## 12. После перезапуска Windows и безопасность

- Сначала запустите **observer_auth.py serve** и убедитесь, что `/healthz` отвечает. Потом проверьте **runtimes status**; если runtime не запущен, восстанавливайте его штатными командами **runtimes connect / run** вашей версии клиента. Не объявляйте успех без `healthy` и `ready`.
- Переменные `$env:CONTROL_PLANE_API_KEY`, `$env:MCP_EXTRA_HEADERS`, `$env:MCP_DISCOVERY_EXTRA_HEADERS` действуют только в **соответствующем окне PowerShell**. В другом окне их потребуется настроить заново, если процесс туннеля надо перезапустить.
- **Не выкладывайте** `.env`, `observer-token`, `observer-authorization-header`, `bridge-config.json`, `bridge-state/`, cookies, AppData, приватные ключи, сессии и журналы с секретами в публичный GitHub, чат или ZIP для другого человека.
- Не храните runtime API key в README или скриптах. Если ключ раскрыт, отзовите его и создайте новый.
- Доступ на чтение папки позволяет передавать данные из неё в ChatGPT. Выдавайте **минимально необходимый каталог**; не делайте корнем весь `C:\` или домашнюю папку.

## 13. Прочие части проекта и подробная документация

| Путь | Назначение |
|---|---|
| `Local-Workspace-Extensions/mcp-host-source/` | Python MCP, `manage.py`, `config.json`, тесты |
| `scripts/observer_auth.py` | Локальный bearer, read-only HTTP-сервер |
| `scripts/status-stack.py` | Диагностика компонентов; не проверяет автоматически живой туннель |
| `INSTALL_FROM_ARCHIVE.ps1` | Установка и офлайн-проверка |
| `chat-launch-smoke-desktop-test/` | Отдельный MCP-демопроект |
| `chat-launch-smoke-desktop-test/web-companion/` | Необязательный bridge, Chain2/Chain3 |
| `chat-launch-smoke-desktop-test/web-extension/` | Расширение Chromium |
| `Local-Workspace-Extensions/experiments/foundation_kernel/` | Экспериментальная база P085, не готовый production-broker |

Дополнительно: [перенос на другой ПК](docs/TRANSFER_TO_NEW_PC_RU.md), [установка из архива](docs/ARCHIVE_INSTALL_RU.md), [отчёт portable QA](docs/PORTABLE_QA_20261111.md), [Hermes monitoring](docs/HERMES_MONITORING_RU.md), [Hermes functional spec](docs/HERMES_FUNCTIONAL_SPEC_RU.md).

**Критерий завершения:** не просто «порт открыт» или «плагин установлен», а **реальный `list_workspaces` и `list_directory` из ChatGPT через работающий туннель**, без передачи секретов и без опасного расширения прав.