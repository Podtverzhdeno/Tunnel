# Tunnel — локальный MCP + ChatGPT-агенты + Web Companion

**Публичный комплект для развёртывания на другом Windows-ПК.** Включает исходники Local Workspace MCP, демонстрационного MCP-приложения New Chat Smoke Test, локального bridge, браузерного расширения и экспериментального Chain2/Chain3.

> **Статус на 04.11.2026:** код и установочные файлы опубликованы как переносимый снимок. **Это не готовая production-автономная фабрика:** Chain3 в trusted-режиме требует отдельно установленного защищённого P085-брокера и независимого подтверждения Windows-полномочий. Непрерывное создание чатов, отсутствие лимитов ChatGPT и полностью самостоятельная работа не гарантируются. Установленные у автора аккаунты, App ID, Tunnel ID, токены и состояния не переносятся.


## Hermes: мониторинг и требования (10.11.2026)

Добавлены [read-only диагностика очереди и план ускорения](docs/HERMES_MONITORING_RU.md),
[функциональная спецификация](docs/HERMES_FUNCTIONAL_SPEC_RU.md) и проверяемый код
в `Hermes-WebGPT/monitoring/`. Это отдельное дополнение к исходникам: действующая
Windows-установка Hermes и архивы от 4 ноября этим изменением не обновляются.

## Перенос на другой ПК — проверенный пакет от 11.11.2026

Для установки готового Local Workspace MCP и подключения к **существующему** Secure MCP Tunnel используйте [пошаговую инструкцию: куда вводить ключ, какой Tunnel ID выбирать и как проверить подключение](docs/TRANSFER_TO_NEW_PC_RU.md).

- [**Tunnel-Portable.zip**](dist/Tunnel-Portable.zip) — безопасно отфильтрованная переносимая копия исходников и проверенного базового установщика (10,4 МБ).
- [**diagnostics.txt**](dist/diagnostics.txt) — версии, контрольная сумма и результаты диагностики.
- [**Tunnel-Portable.sha256**](dist/Tunnel-Portable.sha256) — SHA-256 для проверки целостности перед распаковкой.
- [**Отчёт о повторной проверке**](docs/PORTABLE_QA_20261111.md) — что действительно проверено, а что требует живого подключения к целевому ПК.

Важно: базовое расширение в архиве — **0.8.5**, обновлённые исходники **0.9.7** хранятся отдельно в `migration-snapshot/` и автоматически не устанавливаются. Ключи и пользовательские профили не входят в архив. Это **публичный репозиторий** — никогда не публикуйте рабочие токены, cookies, `.env` или конфигурацию браузера.

## Готовые ZIP-архивы для другого пользователя

Можно не использовать `git clone`: скачайте ZIP и распакуйте его на Windows.

| Архив | Что внутри |
|---|---|
| [Tunnel-Complete-Windows.zip](dist/Tunnel-Complete-Windows.zip) | Архивная базовая сборка от 04.11.2026. Для нового ПК **предпочитайте [Tunnel-Portable.zip](dist/Tunnel-Portable.zip)** и новую инструкцию |
| [Local-Workspace-MCP.zip](dist/Local-Workspace-MCP.zip) | Local Workspace MCP и локальная авторизация |
| [New-Chat-Smoke-Test.zip](dist/New-Chat-Smoke-Test.zip) | Исходники второго MCP-приложения с `plugin.json` и `mcp.json` |
| [Web-Companion-Extension.zip](dist/Web-Companion-Extension.zip) | Расширение Chromium и локальный bridge |

**Проверка:** [SHA-256 всех архивов](dist/SHA256SUMS.txt). [Подробная инструкция по установке из ZIP](docs/ARCHIVE_INSTALL_RU.md).

После распаковки полного архива откройте PowerShell в папке `Tunnel` и запустите:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_FROM_ARCHIVE.ps1
```

Установщик проверит файлы, настроит безопасный локальный read-only MCP, создаст новые токены **на компьютере нового пользователя** и подготовит расширение. **API key, Tunnel ID и приложение в ChatGPT пользователь создаёт самостоятельно**, как описано ниже. ZIP с исходниками **нельзя гарантированно импортировать прямо в ChatGPT как уже зарегистрированный плагин**.

## Содержание репозитория

| Путь | Что это |
|---|---|
| `Local-Workspace-Extensions/mcp-host-source/` | Python MCP-сервер локального рабочего пространства: файловые операции, тесты, jobs, диагностика, исходники и unit-тесты. **Это проверяемый source mirror**, а не слепок живой Windows-службы. |
| `Local-Workspace-Extensions/experiments/foundation_kernel/` | Экспериментальная база P085-брокера, политики и Windows IPC, без ключей и настроенных учётных записей. |
| `Local-Workspace-Extensions/p085_*.py`, `*.ps1` | Проверки и подготовительные скрипты P085. Не запускать с правами администратора из непроверенного клона. |
| `Local-Workspace-Extensions/skills/spec-workflow/` | Навык локальной работы с OpenSpec/SDD. |
| `chat-launch-smoke-desktop-test/` | Отдельное MCP-приложение **New Chat Smoke Test**: `plugin.json`, `mcp.json`, `server.py`, `panel.html`. |
| `chat-launch-smoke-desktop-test/web-companion/` | Локальный Python HTTP-bridge, очереди и контроллеры Chain2/Chain3. |
| `chat-launch-smoke-desktop-test/web-extension/` | Распаковываемое расширение Chromium/Chrome/YaBrowser (Manifest V3). |
| `scripts/install-local.ps1` | Первичная установка конфигураций без публикации секретов. |

## Быстрый старт: проверка чистой установки за один запуск

Сначала клонируйте репозиторий и запустите **офлайн-проверку без подключения к ChatGPT**:

```powershell
git clone https://github.com/Podtverzhdeno/Tunnel.git
cd Tunnel
py -3 scripts\verify-portable-install.py
```

Успех: JSON `"ok": true`, доступно 28 инструментов локального MCP, пройдены тесты New Chat Smoke Test, создан новый **временный** ключ для тестового bridge и проверена неактивная Chain3. Скрипт работает в очищаемом временном каталоге и **не** устанавливает реальные плагины, не связывается с OpenAI, не создаёт чаты, не запускает службы. Для фактической установки используйте шаг 2 ниже.

На Windows можно дополнительно **дважды протестировать сам PowerShell-установщик** в одноразовой копии, не изменяя рабочий конфиг:

```powershell
py -3 scripts\\verify-portable-install.py --test-powershell
```

Результат `"powershell_installer": "passed_twice_idempotent"` подтверждает первый и повторный запуск. Этот тест локально вызывает PowerShell только внутри временной папки без установки служб или доступа к ChatGPT.

**Два пути:**

- **Локальная проверка/демо:** Python, MCP observer, браузерное расширение; ключ OpenAI API не нужен.
- **Подключение к ChatGPT:** собственный Platform Secure MCP Tunnel, разрешение на custom MCP и работающий `tunnel-client`. Полноценный режим записи доступен не на каждом тарифе.

## 0. Что нужно до установки

- **Windows 10/11** с PowerShell, **Python 3.10+**, Git, браузер Chrome/YaBrowser/другой совместимый Chromium.
- Учётная запись ChatGPT, на которой разрешено создание пользовательских MCP-плагинов. Возможность добавлять custom MCP зависит от тарифа и прав рабочего пространства.
- Для удалённого обращения ChatGPT к **локальному MCP** — поддерживаемый **Secure MCP Tunnel**, создаваемый через OpenAI Platform. Нельзя просто передать ChatGPT `http://127.0.0.1:8765/mcp` из своего компьютера.
- Для Web Companion нужен браузер, в котором пользователь **сам вошёл в ChatGPT**, и установленное расширение; **OpenAI API key для работы через веб-интерфейс не нужен**.
- Подписка ChatGPT и платные запросы OpenAI API — **разные продукты** с разными квотами и оплатой.

Официальные инструкции: [создание custom MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server), [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [API keys](https://developers.openai.com/api/docs/guides/api-keys), [установка unpacked extension](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world).

**Доступность по планам (проверено 04.11.2026):** создание и подключение custom MCP зависит от ChatGPT-плана и роли. По [официальной справке OpenAI](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt), полная работа MCP с операциями записи доступна в Business/Enterprise/Edu при соответствующих правах; Pro поддерживает custom MCP с ограниченными возможностями чтения/получения. Для Plus/Go/Free не следует обещать создание собственных полнофункциональных MCP-плагинов. Проверяйте настройки `Plugins` своего аккаунта **до** покупки чего-либо. Репозиторий и локальная проверка доступны независимо от тарифа.

## 1. Клонировать проект

Откройте **PowerShell**:

```powershell
git clone https://github.com/Podtverzhdeno/Tunnel.git
cd Tunnel
py -3 --version
git status
```

Если `py` не найден, установите Python с https://www.python.org/downloads/windows/ и повторите. Команды ниже можно заменить на `python`, если он соответствует версии 3.10+.

Создайте **отдельную рабочую папку** вне системных каталогов, например `C:\Work\AgentProjects`. Не выдавайте MCP доступ ко всему диску, домашней папке или профилю браузера.

## 2. Быстрая локальная подготовка (Windows)

```powershell
$projects = Join-Path $env:USERPROFILE "AgentProjects"
New-Item -ItemType Directory -Force -Path $projects | Out-Null
powershell -NoProfile -File .\scripts\install-local.ps1 -ProjectsPath $projects
```

Скрипт безопасно:
1. создаёт `Local-Workspace-Extensions/mcp-host-source/config.json` со списком доступных папок, если его нет;
2. добавляет alias `projects` только для указанной папки (по умолчанию **read-only**; опционально `-AllowWrite`);
3. создаёт **отдельный** личный bearer для read-only MCP HTTP в `mcp-host-source/.local-mcp-state/`, без его вывода в терминал;
4. создаёт **другой** случайный ключ для web bridge в `web-companion/bridge-state/token`;
5. копирует `bridge-config.json` в распаковываемое расширение;
6. запускает локальную диагностику. Скрипт **не** создаёт ChatGPT-плагины, API-ключи или Windows-аккаунты и **не** публикует секреты.

Не загружайте созданные файлы `config.json`, `bridge-config.json`, `bridge-state/` в GitHub!


**Проверка нового клона:** `py -3 scripts\\verify-portable-install.py` должна вернуть `"ok": true`. Если она не пройдена, **не** подключайте этот каталог к ChatGPT или браузеру. На Windows скрипт PowerShell можно дополнительно прогнать в изолированной копии исходников; запуск из README остаётся ручным действием владельца.

## 3. Local Workspace MCP — сервер на своём ПК

В первом окне PowerShell **из корня клонированного Tunnel**:

```powershell
py -3 scripts\observer_auth.py check
py -3 scripts\observer_auth.py serve --port 8765
```

`observer_auth.py` читает личный ключ из `Local-Workspace-Extensions/mcp-host-source/.local-mcp-state/` и запускает MCP **в том же процессе**. Окно остаётся открытым. Скрипт не печатает ключ; `Ctrl+C` останавливает сервер. Простая команда `manage.py serve` без ключа **не работает**: сервер специально требует `LOCAL_MCP_OBSERVER_BEARER`.

Если ранее запускали старую версию инсталлятора, из корня проекта выполните `py -3 scripts\observer_auth.py init`, затем `check`. Повторный `init` не меняет уже созданный секрет.

Адрес MCP на **этом же** компьютере: `http://127.0.0.1:8765/mcp`. Оставьте окно запущенным.

По умолчанию `observer` предоставляет ограниченную read-only поверхность. Наличие `read/write/execute/git` флагов у папки **не означает**, что эти операции автоматически доступны через удалённый туннель. `legacy` HTTP-профиль специально не допускает привилегированное исполнение без доверенного хоста. Снятие ограничений путём правки флага — не безопасная установка.

**Ручная настройка без скрипта** (альтернатива шагу 2):

```powershell
cd Local-Workspace-Extensions\mcp-host-source
py -3 manage.py init
py -3 manage.py add-root --name projects --path "C:\Work\AgentProjects"
py -3 manage.py doctor
cd ..\..
py -3 scripts\observer_auth.py init
```

Чтобы сознательно разрешить локальные операции с файлами и тестами в выделенной папке, используйте `--write --execute --git` при добавлении корня. Это **не** включает привилегированную публикацию в ChatGPT.

### Статус всего локального стека (ничего не запускает)

Из корня репозитория:

```powershell
py -3 scripts\status-stack.py
```

Команда проверяет авторизованный локальный MCP, bridge, свежесть heartbeat расширения, Chain2/Chain3 и выдаёт список блокеров. Она **не отправляет сообщений, не создаёт агентов, не открывает вкладки и не печатает секреты**. Соединение с `tunnel-client` проверяйте отдельно командой `tunnel-client doctor`.

Если Chain2 зависла в `ACTIVE`, используйте отдельную read-only диагностику:

```powershell
py -3 scripts\chain2-triage.py
```

Скрипт разделяет причины на `browser_delivery_uncertain`, `handoff_invalid`, `handoff_ready_for_role_review` и `worker_evidence_incomplete`. Он **никогда не отправляет сообщение повторно**, не создаёт следующий job и не трактует локальный отчёт как доверенную авторизацию. При `browser_delivery_uncertain` владелец должен сверить исходный чат и подтверждение отправки; автоматическое повторение `UNKNOWN` может создать дубликат. Тесты: `py -3 scripts\test_chain2_triage.py`.

## 4. Где получить API-ключи, Tunnel ID и что именно нужно

| Значение | Где получить | Для чего | Публиковать? |
|---|---|---|---|
| **OpenAI API key для модельных API-запросов** | [OpenAI Platform → API keys](https://platform.openai.com/api-keys) → Create new secret key | В PowerShell: `$env:OPENAI_API_KEY`. **Не нужен для веб-ChatGPT, браузерного расширения и данного готового MCP-сервера** | Никогда |
| **Tunnel runtime API key** | [Platform → Organization Runtime API keys](https://platform.openai.com/settings/organization/api-keys), права **Tunnels Read + Use** | В PowerShell с `tunnel-client`: `$env:CONTROL_PLANE_API_KEY` | Никогда |
| **Tunnel ID** (`tunnel_...`) | [Platform → Tunnels](https://platform.openai.com/settings/organization/tunnels), создать свой туннель; нужны права Tunnels Read + Manage | Параметр `--tunnel-id` при создании профиля или `$env:CONTROL_PLANE_TUNNEL_ID` | Не использовать чужой |
| **Local Web Companion bridge token** | Автоматически создаётся `web-companion/setup_bridge.py` | `web-companion/bridge-state/token` → `build_clean_extension.py` создаёт приватный `web-extension/bridge-config.json`. **Вручную вставлять не требуется** | Никогда |
| **GitHub token (необязателен)** | [GitHub Personal access tokens](https://github.com/settings/personal-access-tokens) | Git Credential Manager при публикации в свой репозиторий; для публичного `git clone` не нужен | Никогда |

### Куда именно вставлять API key и токены (Windows)

**1. API key для отдельного LLM/API-агента — необязателен.** Получите его на [OpenAI Platform → API keys](https://platform.openai.com/api-keys). Он **не нужен** для ChatGPT через браузер и не используется готовым `Tunnel` без отдельного API-worker:

```powershell
# Только если вы отдельно подключили собственный API-исполнитель:
$env:OPENAI_API_KEY = Read-Host "Вставьте ваш OpenAI API key"
# После этого запускайте СВОЙ API-скрипт из этого же окна PowerShell.
```

**2. Runtime API key для Secure MCP Tunnel — обязателен при подключении туннеля.** Создайте в [Organization Runtime API keys](https://platform.openai.com/settings/organization/api-keys) с Tunnels Read + Use. Укажите в **том же окне PowerShell**, где запустите `tunnel-client`:

```powershell
$env:CONTROL_PLANE_API_KEY = Read-Host "Вставьте ваш Tunnel runtime API key"
$tunnelId = Read-Host "Вставьте полный Tunnel ID (tunnel_...)"
# Используйте $tunnelId в команде tunnel-client init --tunnel-id $tunnelId
```

Это **два разных назначения ключей**: `OPENAI_API_KEY` — оплачиваемые вызовы моделей; `CONTROL_PLANE_API_KEY` — доступ клиента к Tunnel control plane. Отдельный `OPENAI_ADMIN_KEY` для обычного `tunnel-client run` не нужен.

**3. Локальный токен Local Workspace MCP — генерируется автоматически.** Из корня репозитория:

```powershell
py -3 scripts\observer_auth.py init
py -3 scripts\observer_auth.py check
```

Он сохраняется в `Local-Workspace-Extensions/mcp-host-source/.local-mcp-state/observer-authorization-header`. **Не копируйте значение вручную**: команда в шаге 5 ниже использует `MCP_EXTRA_HEADERS="Authorization: file:..."` и `MCP_DISCOVERY_EXTRA_HEADERS`, чтобы `tunnel-client` сам прочитал приватный файл. Этот токен **не** нужно вводить в OpenAI Platform, ChatGPT или браузерное расширение.

**4. Локальный токен Web Companion — тоже генерируется автоматически.**

```powershell
cd chat-launch-smoke-desktop-test\web-companion
py -3 setup_bridge.py
py -3 build_clean_extension.py
cd ..\..
```

Один локальный секрет окажется в `web-companion/bridge-state/token`, а соответствующая конфигурация — в `web-extension/bridge-config.json`. После генерации загрузите распакованное расширение из `web-extension/` (шаг 8). Вручную вводить токен в расширение не нужно. **Не копируйте файл `bridge-config.json` на чужой компьютер** — другой пользователь генерирует свой ключ.

**Где хранить:** переменные `$env:...` действуют только в текущем окне PowerShell; не размещайте настоящие ключи в README, `plugin.json`, `mcp.json`, JS, Git, скриншотах и чате. При публикации репозитория локальные `.local-mcp-state/`, `bridge-state/` и `bridge-config.json` должны оставаться исключёнными из Git. **Токены авторизации ChatGPT и cookies вообще не нужны.**

API-ключ нельзя «скачать из ChatGPT»: **его создают в OpenAI Platform**, полный секрет обычно виден один раз. Лучше выпускать отдельный ограниченный ключ для туннеля, а не использовать общий административный ключ. API-ключи нельзя вставлять в `manifest.json`, JS, README, Git, чат, скриншоты или передавать другому пользователю.

**Важно:** использование веб-интерфейса ChatGPT через расширение **не требует `OPENAI_API_KEY`**. Ключ для туннеля требуется только при подключении частного MCP через Secure MCP Tunnel. Владелец другого компьютера должен создать **свои** ключи, туннель и plugin/app.

## 5. Установить Secure MCP Tunnel

1. В [OpenAI Platform → Organization → Tunnels](https://platform.openai.com/settings/organization/tunnels) выберите правильную организацию и откройте **Tunnel settings**. Создайте собственный туннель и сохраните его `tunnel_id`. Нужны права Tunnels Read + Manage для создания; Read + Use для клиента.
2. Скачайте `tunnel-client` из Platform или с [официальной страницы релизов](https://github.com/openai/tunnel-client/releases/latest). Не скачивайте «готовые API-ключи» со сторонних сайтов.
3. Создайте ключ в [Organization Runtime API keys](https://platform.openai.com/settings/organization/api-keys) с **Tunnels Read + Use**. В отдельном PowerShell задайте runtime key **локально**, не добавляя его в файлы проекта:

```powershell
$env:CONTROL_PLANE_API_KEY = Read-Host "Вставьте свой Tunnel runtime API key"
$tunnelId = Read-Host "Вставьте ваш Tunnel ID (tunnel_...)"
tunnel-client help quickstart
```

4. В соответствии с подсказкой вашей версии `tunnel-client` создайте профиль. Для локального HTTP MCP endpoint:

```powershell
tunnel-client init --sample sample_mcp_remote_no_auth --profile local-workspace --tunnel-id $tunnelId --mcp-server-url "http://127.0.0.1:8765/mcp"

# Из КОРНЯ клона: статический HTTP Authorization локальному observer MCP.
# Это НЕ ключ OpenAI Platform; файл создаётся installer/observer_auth.py.
$headerFile=(Resolve-Path ".\Local-Workspace-Extensions\mcp-host-source\.local-mcp-state\observer-authorization-header").Path
$env:MCP_EXTRA_HEADERS="Authorization: file:$headerFile"
$env:MCP_DISCOVERY_EXTRA_HEADERS="Authorization: file:$headerFile"

tunnel-client doctor --profile local-workspace --explain
tunnel-client run --profile local-workspace
```

**Зачем два заголовка:** локальный MCP требует `Authorization: Bearer ...` для `POST /mcp`; `tunnel-client` поддерживает [`mcp.extra_headers` и `mcp.discovery_extra_headers`](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md), в том числе безопасную загрузку значения из `file:/путь`. Указанные переменные действуют **только в текущем окне PowerShell**, не коммитьте их содержимое. Если клиент или custom app переопределяет заголовок `Authorization`, статическое значение может быть вытеснено: настройте ожидаемую аутентификацию, не выключайте проверку на MCP-сервере. Это read-only токен, **не** P085/Trusted удостоверение.

`--sample` и поддерживаемые флаги могут меняться: если команда не принимается, используйте `tunnel-client help quickstart` и [текущий официальный runbook](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels); **не подменяйте ошибки фальшивым успехом**.

5. Убедитесь, что локальный MCP сервер и `tunnel-client` работают в двух окнах. В `doctor` должна быть подтверждена готовность. На новом ПК нужен **свой** `tunnel_id` и API-ключ. Ничего не надо открывать наружу через проброс порта.

## 6. Создать приложение «Local Workspace» в ChatGPT

Это **не** копирование чужого App ID: приложение создаётся заново под аккаунтом и полномочиями нового пользователя.

1. Откройте ChatGPT на вебе → **Plugins** → `+` → **Add custom MCP server** (название пунктов зависит от интерфейса и прав).
2. Name: `Local Workspace`. Description: `Локальные проекты через MCP + Secure Tunnel`.
3. Connection: **Tunnel**, укажите **свой** туннель `local-workspace` из шага 5.
4. Authentication: выберите поддерживаемую локальным сервером схему; не копируйте авторизацию/ключи автора.
5. Ознакомьтесь с предупреждением о рисках и подтвердите создание. Проверьте обнаруженные инструменты.
6. Установите/подключите приложение к чату; запросите `list_workspaces`, затем безопасный `list_directory`. Если приложения или инструментов нет, проверьте права workspace, tunnel health и профиль MCP.
7. Только после успешного read-only теста рассмотрите другие разрешения. Настройка «разрешить все действия» не отменяет серверные ограничения и меры безопасности.

**Ограничение планов:** возможность создавать собственные custom MCP и вызывать инструменты записи зависит от доступных функций, политики организации и тарифа; этот репозиторий не расширяет лимиты или права ChatGPT.

## 7. Создать «New Chat Smoke Test»

В `chat-launch-smoke-desktop-test/` лежат исходники второго, **отдельного** MCP-приложения.

Проверка сервера локально:

```powershell
cd chat-launch-smoke-desktop-test
py -3 verify_plugin.py
```

Файлы:
- `plugin.json` — имя, описание, метаданные интерактивного приложения;
- `mcp.json` — stdio-конфигурация с `${PLUGIN_ROOT}/server.py`;
- `server.py` — один MCP tool `show_new_chat_test`;
- `panel.html` — интерактивная тестовая панель.

Для подключаемой через ChatGPT версии создайте **ещё один** собственный MCP-туннель/профиль `new-chat-smoke`, направленный на stdio-команду `python <ABSOLUTE_PATH_TO_REPO>\chat-launch-smoke-desktop-test\server.py`, по официальной инструкции `tunnel-client`. Затем в Plugins → Add custom MCP server выберите **этот** туннель и имя `New Chat Smoke Test`.

**Нюанс:** `ui/message target=new` — экспериментальная MCP Apps UI-возможность. На Desktop она может поддерживаться; в обычном веб-ChatGPT **создание нового чата самим инструментом не гарантируется**. Панель и сервер не получают скрытые ChatGPT API. Наличие `plugin.json` в GitHub **не создаёт автоматически** приложение в чужом аккаунте.


### Что переносится, а что нет

Из GitHub переносятся **код и метаданные** приложений. У другого пользователя появятся **свои** App ID, Extension ID, адрес туннеля, токены и настройки Plugins. Нельзя импортировать действующую авторизацию или копировать пользовательские идентификаторы/токены из чужого профиля. Исходный `Local Workspace` из автора используется как локальная проверяемая копия; его привилегированная production-инсталляция не входит в репозиторий.

## 8. Установить браузерное расширение Web Companion

Откройте **третье** окно PowerShell:

```powershell
cd chat-launch-smoke-desktop-test\web-companion
py -3 setup_bridge.py
py -3 build_clean_extension.py
py -3 start_bridge.py
py -3 status.py
```

- `setup_bridge.py` генерирует/проверяет **личный** секрет, не печатает его.
- `build_clean_extension.py` копирует 8 разрешённых файлов в соседнюю папку `web-extension/`, в том числе **частный** `bridge-config.json`.
- `start_bridge.py` запускает bridge на `127.0.0.1:18763`, не открывая чатов.
- Если bridge сообщает `already_running`, новый запуск не нужен.

В браузере откройте `chrome://extensions` (для YaBrowser: `browser://extensions`), включите **Режим разработчика** → **Загрузить распакованное** → выберите **именно** папку `chat-launch-smoke-desktop-test/web-extension`, а не `web-companion`.

Откройте https://chatgpt.com, войдите **в свой** аккаунт и проверьте статус через popup расширения/команду `py -3 status.py`. У новых пользователей extension ID будет другим. Не переносите из старого ПК `chrome.storage`, cookies, localStorage, сессии или чужие токены. После обновления файлов расширения нажимайте **Обновить** на странице расширений.

**Первый тест:** только одна новая небольшая задача, отправленная через документированный интерфейс очереди. Не восстанавливайте старые неподтверждённые отправки повторным кликом — сначала проверьте подтверждение `message_saved` и `REPORT_ACKED`. Не используйте расширение для обхода лимитов или массового создания пустых чатов.

## 9. Chain2 / Chain3 и режимы

```powershell
cd chat-launch-smoke-desktop-test\web-companion
py -3 chain_campaign.py preview
py -3 chain3_continuous.py mode
py -3 chain3_continuous.py preview
```

Режим выбирается **перед первым arm/jobs**:

```powershell
py -3 chain3_continuous.py select-mode --mode sandbox --confirm-mode-selection
# Или: --mode trusted
```

- `sandbox`: обычный экспериментальный режим, задача → разработчик → ревьюер, изолированные каталоги, ограничения очереди/частоты, SHA-ACK.
- `trusted`: выбор **полностью доверенной политики**, но `selected_mode=trusted` сам по себе **не доказывает** доверие. Пока P085 не установлен и не протестирован независимо, будет `effective_mode=blocked`.

**В текущем исходнике Chain3 дополнительно ждёт принятия исходной Chain2.** Нельзя считать `generation_limit=null` гарантией бесконечной полезной работы; при отсутствии подтверждённого нового дефекта цепочка корректно останавливается. Автоматизация зависит от доступного LLM-исполнителя и его лимитов. Репозиторий **не включает** чужие очереди/ACK/отчёты и не создаёт чужие чаты при клонировании.

## 10. Trusted/P085: что НЕ происходит автоматически

Исходники в `Local-Workspace-Extensions/` — не доверенная инсталляция Windows. Публикация GitHub и выбор `trusted` не доказывают реальный SID, ACL, защищённую службу, ключ подписи политики или независимую проверку двух пользователей.

Для такого режима владелец ПК должен отдельно и осознанно проверить защищённый пакет и его хеши, настроить отдельные Windows-учётные записи и права, установить защищённую службу, выполнить реальные негативные E2E-тесты и только затем разрешать привилегированные операции. **Не запускайте скрипты установки P085 с повышением прав непосредственно из изменяемого каталога клона без независимой проверки.**

В текущем опубликованном коде **полный trusted cutover и независимая E2E-проверка не выполнены**.

## 11. Проверка исходников и быстрый smoke-test

В корне клона:

```powershell
py -3 scripts\test_observer_auth.py
py -3 scripts\verify-portable-install.py --test-powershell
```

Второй тест проверяет реальный локальный HTTP: без bearer — **401**, с правильным bearer — **200**. Он также дважды запускает установщик на временной копии и убеждается, что ключи не перезаписываются. Никаких обращений к ChatGPT нет.

```powershell
cd Local-Workspace-Extensions\mcp-host-source
py -3 -m unittest discover -s tests -p "test_*.py"
py -3 manage.py doctor

cd ..\..\chat-launch-smoke-desktop-test
py -3 verify_plugin.py

cd web-companion
py -3 test_chain3_continuous.py
py -3 chain3_continuous.py preview
```

Для полного запуска тестов Web Companion: `py -3 -m unittest discover -p "test_*.py"`. При подготовке переносимой версии 4 ноября 2026 года проверено **204 теста: 203 PASS, 1 SKIP** на чистом опубликованном наборе. Дополнительно MCP host mirror: **235 тестов: 229 PASS, 6 SKIP**; New Chat Smoke Test: **9 PASS**. JavaScript-тесты `node test_core.js`, `node test_background.js`, `node test_recovery.js`, `node test_background_content.js` также прошли. Смотрите актуальные результаты после клонирования — версии и среда могут отличаться.

## 12. Типичные проблемы

| Симптом | Что проверить |
|---|---|
| `No roots configured` | Выполните шаг 2 или `manage.py add-root`. |
| MCP не виден из ChatGPT | `tunnel-client doctor`, работающие процессы, соответствие Platform organization/ChatGPT workspace, наличие прав Plugins. |
| MCP `OBSERVER_HTTP_AUTH_TOKEN_REQUIRED` или HTTP 401 | Выполните `py -3 scripts\observer_auth.py init`, запуск только через `observer_auth.py serve`; для туннеля настройте `MCP_EXTRA_HEADERS` и `MCP_DISCOVERY_EXTRA_HEADERS` на приватный файл `observer-authorization-header`. |
| `403 unauthorized` у bridge | `setup_bridge.py`, `build_clean_extension.py`, обновление расширения, `bridge-config.json` с тем же ключом, что в `bridge-state/token`. |
| Расширение не отправляет | Состояние входа в ChatGPT, правильная версия расширения, разрешения, `status.py`, cooldown/причины в диагностике. |
| Chain3 `owner_armed=false` | Отдельная процедура owner arm и принятие Chain2 ещё не завершены. |
| Chain3 `effective_mode=blocked` | Выбран trusted без верифицированного OS-брокера P085. Не подменяйте проверки флагом. |
| Задача зависла после `send_clicked` | Не отправляйте её повторно без сверки реального сохранения сообщения и ACK. |
| `py` не найден | Установите Python и проверьте PATH. |
| Не работает custom MCP на тарифе | Проверьте текущие ограничения ChatGPT Plugins, права рабочего пространства и доступность функции. |

## 13. Где лежат приватные файлы и что нельзя коммитить

`.gitignore` исключает `**/bridge-state/**`, `**/.local-mcp-state/**`, `**/bridge-config.json`, `**/config.json`, `.env`, `**/.agent-team/**`, локальные отчёты, сессии, credentials и внутренние состояния. **Не копируйте** свои ключи в файлы README или issue. При компрометации ключа выпустите новый ключ, отзовите старый и смените bridge-конфигурацию.

Публичный репозиторий содержит **исходный код и установочные шаблоны**, не приватные сессии или готовые авторизованные экземпляры приложений. Проверяйте каждый сторонний fork перед предоставлением прав на запуск локального кода.

## 14. Поддержка и ограничения

Это экспериментальная система интеграции с локальным ChatGPT UI, а не официальная ChatGPT multi-chat API. Изменения DOM, браузера, MCP SDK и API/планов могут потребовать обновления. Никаких обещаний бесконечной бесплатной генерации, автономного обхода лимитов или успешной настройки P085 без участия владельца.

Если система не проходит стартовую диагностику, создайте issue с версией Python/браузера, кодом ошибки, **без** ключей, логов с личными данными и идентификаторов сессий.


## 15. Следующие этапы развития

Смотрите [проверяемый план оставшейся работы](docs/STATUS_AND_ROADMAP.md): безопасный Trusted/P085, полная приёмка Chain2, тест настоящего подключённого аккаунта, контролируемое длительное исполнение и измерение реальных токенов. Ни одна из этих функций не становится автоматически разрешённой после публикации исходников.

