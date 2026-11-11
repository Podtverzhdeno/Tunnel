# Перенос Local Workspace MCP на другой Windows-ПК и подключение к Secure MCP Tunnel

**Дата проверки:** 11 ноября 2026.
**Источник:** [Podtverzhdeno/Tunnel](https://github.com/Podtverzhdeno/Tunnel).
**Назначение:** установка готового Local Workspace MCP без переноса ключей, cookies, браузерных сессий и пользовательских прав исходного ПК.

> **Важное различие:** `Tunnel-Portable.zip` включает **проверенную базовую установку** (Web Companion 0.8.5) и отдельно **отложенный снимок более свежих исходников** (в том числе 0.9.7) внутри `Tunnel/migration-snapshot/`. Новые исходники **не подменяют установленные автоматически**: они могут иметь другие зависимости и требуют индивидуальных тестов. Перенос кода не означает перенос работающей авторизации, Windows-служб и пользовательских прав.

## Проверка прав до скачивания

**Сначала убедитесь, что ваш аккаунт действительно позволяет создавать пользовательские MCP-подключения.** По [актуальной справке OpenAI](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt), полный custom MCP доступен Business и Enterprise/Edu (для Business — администратору/владельцу; для Enterprise/Edu — также авторизованным участникам по RBAC), а в Pro есть ограничение на read/fetch. Для обычного Plus право самостоятельно создавать custom MCP **не подтверждено этой документацией**; наличие ранее подключённого плагина или локального MCP на старом ПК не доказывает, что новый аккаунт/рабочее пространство позволит создать соединение. Проверьте **ChatGPT → Plugins → + → Add custom MCP server** до установки. Если пункт недоступен, остановитесь: перенос исходников не исправит ограничение аккаунта.

В настройках туннеля проверьте также **ассоциацию с нужным ChatGPT workspace**: наличие Tunnel ID в OpenAI Platform само по себе не гарантирует, что его покажет ChatGPT. Нужны разрешения Tunnels Read + Use и у runtime key, и у оператора подключения.

## 0. Что взять из репозитория

1. [`dist/Tunnel-Portable.zip`](https://github.com/Podtverzhdeno/Tunnel/blob/main/dist/Tunnel-Portable.zip) — основной ZIP для нового компьютера.
2. [`dist/diagnostics.txt`](https://github.com/Podtverzhdeno/Tunnel/blob/main/dist/diagnostics.txt) — отчёт и контрольная SHA-256.
3. Этот документ — установка и проверка.

Для прямого скачивания на GitHub откройте файл и нажмите **Download raw file**. Репозиторий публичный — **для скачивания токен GitHub не нужен**. Не клонируйте весь рабочий каталог старого ПК. На новом ПК используйте отдельную папку (например, `C:\Work\Tunnel`) и отдельную папку для документов, к которым MCP получит доступ.

Проверьте ZIP до распаковки:

```powershell
Get-FileHash "$env:USERPROFILE\Downloads\Tunnel-Portable.zip" -Algorithm SHA256
```

Сравните значение с `SHA256:` из `diagnostics.txt` или с `dist/Tunnel-Portable.sha256` в GitHub. При несовпадении не устанавливайте архив. После распаковки **в первую очередь откройте этот же документ внутри ZIP**: `Tunnel\docs\TRANSFER_TO_NEW_PC_RU.md`. Не руководствуйтесь устаревшим руководством от 4 ноября, если оно противоречит этому файлу.

## 1. Что должно быть на новом ПК

- Windows 10/11, PowerShell; Python 3.10+ (рекомендуемая проверенная на исходном ПК версия — Python 3.14.10); `py -3 --version`.
- Node.js не требуется для основного Python MCP, но для отдельных JS-тестов и расширений полезен. На исходном ПК: Node 24.14.0, npm 11.9.0.
- Интернет для первого получения `tunnel-client` и связи с Platform; Google Chrome / Chromium / Яндекс Браузер для Web Companion.
- **Существующий на целевом аккаунте Tunnel ID**, если вы уже создали туннель. **Новый туннель создавать не нужно** — используйте уже созданный.
- Доступ к созданию custom MCP server в целевом рабочем пространстве ChatGPT и права Tunnels Read + Use в соответствующей организации OpenAI Platform. Эти разрешения отдельны от факта наличия ChatGPT Plus.
- Папка проекта, например `C:\Work\AgentProjects`. Не предоставляйте по умолчанию доступ ко всему `C:\Users`, диску `C:` или профилю браузера.

**Если этот же Tunnel ID ещё обслуживается старым компьютером, сначала остановите на нём `tunnel-client run`** для контролируемого переключения. Не переносите профили авторизации и приватные ключи между ПК.

## 2. Распаковка и проверка

В PowerShell:

```powershell
$destination = "C:\Work"
if (Test-Path "$destination\Tunnel") {
    throw "Папка C:\Work\Tunnel уже существует. Не перезаписывайте её: выберите другой пустой каталог."
}
New-Item -ItemType Directory -Force $destination | Out-Null
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Tunnel-Portable.zip" -DestinationPath $destination
Set-Location "$destination\Tunnel"
py -3 --version
py -3 scripts\verify-portable-install.py
```

Успешная офлайн-проверка: JSON с `"ok": true`, **28 инструментами MCP**, тестами авторизации HTTP **401 без локального bearer / 200 с bearer**. Проверка не подключает реальный ChatGPT и не требует API-ключа.

Если архив уже распакован и содержит вложенную папку `Tunnel`, переходите именно в неё: `INSTALL_FROM_ARCHIVE.ps1` и `scripts` должны быть рядом.

## 3. Первичная локальная установка

В PowerShell из `C:\Work\Tunnel`:

```powershell
# Сначала просмотрите установочный скрипт.
notepad .\INSTALL_FROM_ARCHIVE.ps1

# Безопасный первый запуск — read-only.
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1 -ProjectsPath "C:\Work\AgentProjects"
```

Что происходит:

1. Создаётся или проверяется `Local-Workspace-Extensions\mcp-host-source\config.json` с alias `projects` на указанную папку.
2. Локально генерируется **observer bearer MCP** в `Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\`. Значение в README или терминал **не выводится**.
3. Локально генерируется **отдельный** секрет Web Companion (`chat-launch-smoke-desktop-test\web-companion\bridge-state\token`) и `web-extension\bridge-config.json`.
4. Проверяется конфигурация. Скрипт не создаёт облачный туннель, приложение ChatGPT, Windows-службу и не публикует ключи.

По умолчанию MCP работает в read-only режиме. Флаг `-AllowWrite` включайте только осознанно: он расширяет локальные разрешения на **конкретную** папку, а не отменяет политику защищённого удалённого доступа. Не включайте trusted/P085 по умолчанию.

Если `projects` уже существует, но ссылается на другую папку, установщик безопасно остановится. Проверьте локальный `config.json`; не переписывайте существующую конфигурацию вслепую.

## 4. Запуск MCP на новом ПК

Откройте **отдельное первое** окно PowerShell:

```powershell
Set-Location C:\Work\Tunnel
py -3 scripts\observer_auth.py check
py -3 scripts\observer_auth.py serve --port 8765
```

Оставьте окно открытым. MCP слушает **только локальный интерфейс** `http://127.0.0.1:8765/mcp`. Этот адрес не открывается в интернете: наружное подключение организует `tunnel-client`, без проброса портов на роутере.

Если `check` сообщает, что авторизация отсутствует, в корне проекта выполните `py -3 scripts\observer_auth.py init`, затем снова `check`. Если порт занят, выясните, какой процесс его использует, прежде чем завершать что-либо.

## 5. Где взять и куда вводить ключи

| Значение | Где получить | Куда вводить | Нужно сейчас? |
|---|---|---|---|
| **Tunnel ID** (`tunnel_...`) | [OpenAI Platform → Tunnels](https://platform.openai.com/settings/organization/tunnels) | Локальная переменная `$tunnelId` и параметр `--tunnel-id` | **Да**, используйте уже созданный ID |
| **Tunnel runtime API key** | [Organization API keys](https://platform.openai.com/settings/organization/api-keys), ключ с правами **Tunnels Read + Use** | `$env:CONTROL_PLANE_API_KEY` в PowerShell с `tunnel-client` | **Да** |
| **Локальный bearer MCP** | Генерирует `observer_auth.py init` | Файл `.local-mcp-state\observer-authorization-header`, подключается через `MCP_EXTRA_HEADERS` и `MCP_DISCOVERY_EXTRA_HEADERS` | Генерируется **автоматически** |
| **Web Companion token** | Генерирует `setup_bridge.py` | `bridge-state\token`, переносится установщиком в локальный `web-extension\bridge-config.json` | **Не вводится вручную** |
| **OpenAI API key** (`OPENAI_API_KEY`) | [Platform API keys](https://platform.openai.com/api-keys) | Только для **отдельного** API-агента | **Нет**, для веб-ChatGPT / MCP не нужен |
| **GitHub token** | GitHub settings | Git Credential Manager, только если нужен `git push` | **Нет**, для скачивания публичного архива не нужен |

**Не вписывайте реальный ключ в** `config.json`, `mcp.json`, `plugin.json`, `README.md`, `manifest.json`, `.ps1` или GitHub. Используйте временную переменную окружения **в том окне PowerShell**, из которого запускаете `tunnel-client`. Реальный ключ останется локально в памяти процесса.

Если существующий ключ утрачен, получите **новый ограниченный runtime key** у администратора Platform/в своей организации. Не используйте `OPENAI_ADMIN_KEY` для обычного `tunnel-client run`.

## 6. Подключение Secure MCP Tunnel

Официальные ссылки:

- [Secure MCP Tunnels — инструкция OpenAI](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [tunnel-client — актуальные релизы](https://github.com/openai/tunnel-client/releases/latest)
- [tunnel-client — конфигурация](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md)

Скачайте **полный** (не `tunnel-client-runtime`) Windows ZIP с [официального выпуска](https://github.com/openai/tunnel-client/releases/latest): для стандартного Intel/AMD Windows выбирайте **windows-amd64**, для Windows ARM — **windows-arm64**. Полный бинарник нужен, потому что только он содержит команды `init`, `doctor` и `profiles`. Не скачивайте бинарники со сторонних сайтов. Сверьте хеш загруженного архива с `SHA256SUMS.txt` того же официального выпуска.

Распакуйте **всю папку** (включая сопутствующие файлы), например в `C:\Tools\tunnel-client`. В новом окне PowerShell найдите `.exe` и добавьте его каталог в PATH **только на время этого окна**:

```powershell
$client = Get-ChildItem "C:\Tools\tunnel-client" -Recurse -File -Filter "tunnel-client.exe" |
    Select-Object -First 1
if (-not $client) { throw "Полный tunnel-client.exe не найден. Проверьте архитектуру и тип скачанного ZIP." }
$env:Path = "$($client.DirectoryName);$env:Path"
tunnel-client --version
tunnel-client help quickstart
tunnel-client help samples
```

Если `tunnel-client` **уже установлен и на этом ПК есть рабочий профиль**, не переустанавливайте и не запускайте `init` поверх него: сначала проверьте текущий профиль командой `doctor --explain` и сопоставьте Tunnel ID и локальный endpoint. Значение PATH, как и временный API key, не сохраняется между окнами PowerShell.

Далее откройте **второе окно PowerShell**, введите свои данные **без сохранения ключа в файл**:

```powershell
Set-Location C:\Work\Tunnel

# Вставьте ID уже созданного туннеля (это не секретный API-ключ).
$tunnelId = Read-Host "Tunnel ID (tunnel_...)"

# Скрытый ввод runtime API key.
$secureKey = Read-Host "Tunnel runtime API key (Tunnels Read + Use)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Variable secureKey, bstr -ErrorAction SilentlyContinue
}

# Создайте профиль, указывающий на локальный MCP.
tunnel-client init --sample sample_mcp_remote_no_auth --profile local-workspace --tunnel-id $tunnelId --mcp-server-url "http://127.0.0.1:8765/mcp"

# Приватный локальный заголовок: читается ИЗ ФАЙЛА, не копируйте значение.
$headerFile = (Resolve-Path ".\Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\observer-authorization-header").Path
$env:MCP_EXTRA_HEADERS = "Authorization: file:$headerFile"
$env:MCP_DISCOVERY_EXTRA_HEADERS = "Authorization: file:$headerFile"

# Диагностика и запуск.
tunnel-client doctor --profile local-workspace --explain
tunnel-client run --profile local-workspace
```

Профиль `local-workspace` может уже существовать на этом ПК. В этом случае **не перезаписывайте его вслепую**: проверьте `tunnel-client help quickstart`, `doctor`, текущие `tunnel_id` и `mcp.server_url`. При изменениях CLI приоритет у `tunnel-client help ...` и актуальной документации OpenAI.

Значение `CONTROL_PLANE_API_KEY` требуется **при каждом новом запуске** `tunnel-client`, если вы используете только временные переменные PowerShell. Не сохраняйте ключ в обычный профиль PowerShell, командную историю, репозиторий или постоянные системные переменные без осознанной политики хранения секретов.

Успех: `doctor` не сообщает о недостающем ключе и туннеле; после `run` клиент показывает готовность и работу канала `main`. Оставьте его запущенным одновременно с MCP. Дополнительно проверьте **readiness**, а не только факт запущенного процесса: откройте локальную служебную страницу `/ui` или `/readyz` на адресе health listener из профиля/логов `tunnel-client` (часто `http://127.0.0.1:8080/readyz`, но порт может отличаться). Не открывайте порт 8765 в роутере.

## 7. Подключить MCP в ChatGPT

На **том аккаунте/рабочем пространстве ChatGPT, к которому привязан туннель**:

1. Откройте **Plugins → + → Add custom MCP server** (формулировки могут отличаться).
2. Имя: `Local Workspace`. Описание: `Локальные файлы и проекты через Secure MCP Tunnel`.
3. **Connection → Tunnel** → выберите существующий туннель либо укажите его `tunnel_id`.
4. В поле **Authentication** выберите **No authentication / None**, если этот вариант доступен: базовый observer MCP **не использует OAuth**, а свой локальный HTTP bearer туннельный клиент подставляет сам из `MCP_EXTRA_HEADERS` и `MCP_DISCOVERY_EXTRA_HEADERS`. Не вставляйте этот bearer в ChatGPT, не выбирайте OAuth без реально настроенного OAuth-сервера. Если интерфейс предлагает другую обязательную схему, проверьте совместимость подключения, прежде чем продолжать.
5. Примите предупреждение о рисках, создайте plugin, изучите список инструментов.
6. В тестовом чате вызовите `list_workspaces` и `list_directory`. Должен быть корень `projects`. Начните с read-only операций.

Если пункта **Add custom MCP server** нет или инструментов нет, проверьте права рабочего пространства/организации и доступность функции для вашего плана. Это нельзя исправить копированием ZIP или вставкой чужого ключа. Подробнее: [справка OpenAI](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## 8. Web Companion и расширение браузера (по необходимости)

Если нужна **браузерная интеграция**, откройте **третье окно PowerShell**:

```powershell
Set-Location C:\Work\Tunnel\chat-launch-smoke-desktop-test\web-companion
py -3 setup_bridge.py
py -3 build_clean_extension.py
py -3 start_bridge.py
```

`start_bridge.py` по умолчанию слушает **127.0.0.1:18763**. Токен браузера уже сгенерирован **на этом ПК**; вводить его в popup или код вручную не нужно.

В Chrome откройте `chrome://extensions`, в Яндекс Браузере — `browser://extensions`. Включите **режим разработчика → Загрузить распакованное**. Выберите:

```text
C:\Work\Tunnel\chat-launch-smoke-desktop-test\web-extension
```

Не выбирайте `web-companion`, `migration-snapshot` или ZIP. Войдите на `https://chatgpt.com` вручную своим аккаунтом. Откройте расширение с панели браузера, а не вручную через `popup.html`.

Из папки `web-companion` выполните:

```powershell
py -3 status.py
```

Сначала проверьте **один небольшой запрос**. Если отправка не подтверждена, не нажимайте повторно без проверки, чтобы не создавать дубликаты. Не переносите с исходного ПК cookies, `chrome.storage`, локальные базы, профили браузера и состояние сессий.

### Важное различие версий

Автоматически установленная **проверенная базовая версия расширения — 0.8.5**. В архиве есть более свежие **исходники 0.9.7** в `migration-snapshot`, но их интеграционная установка **не проверена**. Для рабочего использования не смешивайте файлы разных версий «по одному»: сначала отдельно соберите и протестируйте согласованный релиз, убедитесь в совместимости `bridge` / `web-extension` / `manifest`, затем обновляйте.

## 9. Итоговая проверка

В **четвёртом** окне PowerShell (или после остановки тестов):

```powershell
Set-Location C:\Work\Tunnel
py -3 scripts\status-stack.py
py -3 scripts\chain2-triage.py
```

Параллельно должны работать:

- Окно 1: `observer_auth.py serve` — MCP на 127.0.0.1:8765;
- Окно 2: `tunnel-client run` — привязка **к существующему Tunnel ID**;
- Окно 3 (опционально): `start_bridge.py` — Web Companion на 127.0.0.1:18763;
- Браузер с локально установленным расширением, если нужен веб-режим.

`status-stack.py` и `chain2-triage.py` не являются подтверждением рабочего туннеля сами по себе: для него отдельно смотрите `tunnel-client doctor --profile local-workspace --explain`. Доступность 28 локальных инструментов в изолированном тесте также **не гарантирует**, что все они разрешены через удалённый read-only профиль.

## 10. Ошибки и решение

| Симптом | Что проверить |
|---|---|
| `py` не найден | Установить Python + launcher, заново открыть PowerShell |
| Нет `observer-authorization-header` | Из корня: `py -3 scripts\observer_auth.py init`; затем `check` |
| MCP 401 / `OBSERVER_HTTP_AUTH_TOKEN_REQUIRED` | `MCP_EXTRA_HEADERS`, `MCP_DISCOVERY_EXTRA_HEADERS`, полный путь к файлу заголовка, правильный запуск через `observer_auth.py serve` |
| `tunnel-client` не находится | Добавить папку с официальным `tunnel-client.exe` в PATH или вызывать по полному пути |
| `doctor` пишет missing runtime key | Переменная `CONTROL_PLANE_API_KEY` должна быть установлена в **том же окне**, где запускается клиент |
| `tunnel_id` не найден / permission denied | Убедиться, что выбрана правильная организация Platform, есть права **Read + Use**, ID принадлежит ей и связан с ChatGPT workspace |
| MCP не появляется в Plugins | Проверить доступность custom MCP в аккаунте и `tunnel-client doctor` |
| Web Companion 403 | Пересоздать локальную связку `setup_bridge.py` + `build_clean_extension.py`, перезагрузить распакованное расширение |
| В расширении старый код | Проверить фактическую версию `manifest.json`, перезагрузить только согласованную сборку |
| Chain3 `blocked` | Это ожидаемый ограничитель до независимой установки защищённого P085 и приёмки Chain2; не обходить проверку |
| Миграция отработала, а старый ПК ещё отвечает | Остановить старый `tunnel-client run` и провести контролируемый cutover |
| Неудачное обновление | Вернуться к проверенному базовому релизу, не смешивать файлы `migration-snapshot` с работающей установкой |

## 11. Мини-чеклист готовности

- [ ] SHA-256 ZIP совпадает с `diagnostics.txt`.
- [ ] `verify-portable-install.py` вернул `ok: true`.
- [ ] `INSTALL_FROM_ARCHIVE.ps1` прошёл без ошибок.
- [ ] Локальный observer MCP работает на 127.0.0.1:8765.
- [ ] Существующий Tunnel ID и новый runtime key заданы **локально**.
- [ ] `tunnel-client doctor` подтверждает готовность профиля.
- [ ] В ChatGPT доступны `list_workspaces` и `list_directory`.
- [ ] Если нужен Web Companion: локальный bridge и расширение работают с ключами **этого ПК**.
- [ ] Реальные ключи, cookies, базы, файлы `.env`, `bridge-config.json` и `.local-mcp-state` отсутствуют в Git.

**Ограничение:** переносимая сборка — не гарантированно production-ready система. Она не переносит чужие App ID, права ChatGPT, Windows-идентичности, защищённую службу P085, живые браузерные авторизации и неподтверждённые очереди старых задач.
