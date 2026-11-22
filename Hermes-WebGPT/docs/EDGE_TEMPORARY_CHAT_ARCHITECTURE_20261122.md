# Hermes / Edge Temporary Chat: аудит, SDD и план приёмки (2026-11-22)

Статус: исследование + изолированные изменения; НЕ production-ready. Базовый HEAD main на момент начала: 350ea33080bc35245aa6741f85fa926cc8a8fcaf. Работа отделена от действующей установки и от открытых PR #6–#8.

## 0. Жёсткие требования владельца

R0. Для новой сессии открывать в точности https://chatgpt.com/?temporary-chat=true, не / и не сохранённый чат. Наличие параметра в адресной строке — только запрос режима, НЕ доказательство, что интерфейс его включил. Перед любой отправкой нужен независимый UI-признак Temporary Chat, с ограниченным временем ожидания и ошибкой fail-closed, если нет подтверждения.

R1. Сессия Hermes не должна завершать весь браузер. Запущенный браузер живёт независимо от отдельного inference turn, MCP-клиента и короткого Playwright/CDP подключения. Закрывать можно только принадлежащую задаче вкладку, после финального подтверждения и лишь в явно выбранной политике; временные сессии по умолчанию сохраняются до команды пользователя.

R2. Не повторять неизвестный Send. Любой сбой после durable intent/armed — UNCERTAIN и ручная сверка, а не новый запуск с тем же содержимым. Не переносить рабочую временную сессию на новый URL или транспорт без подтверждения.

R3. CPU оптимизировать без отключения sandbox, GPU, site isolation, SmartScreen, TLS и без удаления необходимых браузерных функций. Профиль Edge должен быть отдельным от личного пользовательского браузера. Приоритет — количество живых вкладок и поведение расширения, а не недокументированные chromium-флаги.

R4. Не считать найденную вкладку принадлежащей Hermes на основании совпавшего адреса, заголовка или текста. Не читать личные вкладки. Не публиковать профиль, auth-токены, браузерный cache или переписку.

## 1. Проверенные факты исходного кода

F01. Основное расширение v0.6.6 лежит в chat-launch-smoke-desktop-test/web-companion; web-extension — зеркальный каталог загружаемого расширения. manifest v3, background.service_worker, разрешения tabs/storage/scripting/alarms и доступ только к loopback bridge + chatgpt.com.

F02. background.js dispatch открывает НЕ временный URL: https://chatgpt.com/#chat-launch-smoke=bg-<uuid>. background_content.js определяет owned job по hash в корне /, затем ожидает /c/... и сохранённое сообщение.

F03. В быстром режиме background_content.js после minHoldSeconds (по умолчанию 5 с для короткого сообщения) выдаёт submitted даже при продолжающейся генерации. background.js finish вызывает chrome.tabs.remove. Это намеренное старое smoke-поведение и одно из объяснений исчезающих вкладок; однако уничтожение всего браузерного процесса требует отдельного живого доказательства.

F04. Для report-mode включены защищённая проверка tab/url/DOM и scoped ACK от bridge; закрытие без подтверждённого report запрещено. Сохранённый /c/ URL — часть идентичности. Подмена одним только temporary-chat=true сломает этот контракт.

F05. core.js повторно обходил документ через querySelectorAll('*'), в том числе Shadow DOM при поиске редактора/кнопки. В этой ветке discovery roots кешируется на 1500 мс для одного документа, но выбор editor/Send остаётся свежим.

F06. В report-mode content script до этой ветки каждые ~500 мс перечитывал элементы страницы, даже после того как событие message_saved было принято работником. В этой ветке он прекращает сканирование после подтверждённой передачи; при потере ACK повторяет только stage, не Send.

F07. Draft PR #8 использует отдельный Yandex/CDP browser (Start-Hermes-CDP-Browser.ps1), открывает обычный chatgpt.com и требует заранее открытый /c/<id>. Поэтому это НЕ готовая реализация временного чата и не может быть переключена заменой одного URL. Playwright browser.close после connect_over_cdp по документации обычно означает disconnect, а не принудительный kill; действительную причину закрытия определять по процессным логам.

## 2. Разделить жизненные циклы

A. User-owned Edge process: Windows interactive session, один устойчивый изолированный user-data-dir, вручную выполненный вход. Держит состояние/вкладки, но не знает ни токены Relay, ни Telegram. Не запускается внутри цикла inference и не уничтожается в finally.

B. Hermes session broker: выдаёт ограниченные leases на вкладки, поддерживает request_id, session_id и состояние транспорта. Ни один новый claim не делается, пока нет idle capacity и подтверждения правильного интерфейса.

C. Browser controller: подключается к уже живому процессу Edge через расширение (предпочтительно для этой задачи) либо CDP в отдельном экспериментальном режиме. Должен явно отличать detach от close и never call close on a user-owned persistent context.

D. Durable request journal: инварианты intent -> armed -> sent_unknown -> confirmed_user -> confirmed_answer -> returned -> delivered. Журнал может быть менее точным относительно стадии после аварии, но никогда не откатывается назад до SEND_ALLOWED. Содержимое промптов не пишется в публичные логи.

E. Telegram/Relay: единственный Gateway + Relay; не заменять существующую цепочку вторым ботом. Подтверждение модели не равно Telegram delivery. ACK результата, ACK Telegram и жизненный цикл вкладки — независимые события.

## 3. Temporary Chat: новая модель идентичности

НЕЛЬЗЯ считать постоянный /c/id необходимым/достаточным признаком временного режима; сохранённая беседа может стать обычной, а временная не обязана быть переоткрываема из истории. Для новой версии:

1) session_id — случайный UUID, создаваемый Hermes ДО открытия вкладки.
2) tab_id/window_id + проверенный profile_id + документный navigation epoch + per-session nonce — временная привязка; tab_id отдельно недостаточен.
3) temporary_ui_verified — отдельный факт, который получает browser controller от видимого UI. Не выводить его из URL query; не фиксировать по чужим DOM-элементам. При редизайне ChatGPT — ошибка needs_manual_verification.
4) request_id — отдельный ID для каждого сообщения, immutable и связан с session_id.
5) Ключ unique (session_id, request_id, send_attempt=1). После intent/armed любое неизвестное завершение переводит в uncertain_no_replay.
6) Tab close/page crash/browser restart в любой post-send стадии: состояние paused_needs_reconciliation; НЕ создавать новый temporary chat автоматически с тем же контекстом и не переотправлять.
7) Manual Save, выключение Temporary, новый логин, навигация на чужой чат и смена аккаунта: остановка auto-send; не восстанавливать скрытно.
8) Временные чаты не должны иметь фиктивную гарантию восстановления после закрытия страницы. Ledger восстанавливает только факты обработки и отказ, но не исчезнувший разговор.

Пример состояний сессии:
  CREATED -> BROWSER_ATTACHED -> TEMP_MODE_VERIFIED -> IDLE
  IDLE -> DRAFT_PREPARED -> INTENT_RECORDED -> SEND_ARMED
  SEND_ARMED -> SEND_UNCERTAIN -> USER_TURN_CONFIRMED
  USER_TURN_CONFIRMED -> RESPONSE_CONFIRMED -> RESULT_ACKED
  RESULT_ACKED -> IDLE (если последующий запрос в той же живой вкладке)
  * -> PAUSED_NEEDS_RECONCILIATION (при смене UI/аккаунта/потере вкладки).
UNKNOWN запрещено переводить в IDLE с повторным Send без отдельного доказательства отсутствия отправки.

## 4. Причина исчезновения окна — матрица гипотез и доказательств

H1. Extension chrome.tabs.remove закрывает последнюю вкладку окна: смотреть telemetry tab_closed, tab_id, window_id и число оставшихся вкладок; не путать с закрытием процесса. В репозитории этот путь подтверждён, но не доказано, что он вызвал наблюдаемый пользователем случай.

H2. Playwright launch_persistent_context + context.close в finally: контекст закрывает запущенный браузер; использовать только независимое присоединение к owner-owned Edge. Сверить точный путь вызовов и created_by.

H3. Известный Edge profile конфликтует с уже открытым Edge и повторно делегирует аргументы другому процессу: отдельный user-data-dir, сравнить PID/command-line до и после запуска.

H4. Родительская Windows job object/обёртка завершает дочерний браузер при выходе процесса: проверить Win32_Process ParentProcessId, ProcessId, exit code и Windows Event Viewer. Не утверждать без наблюдения.

H5. Crash/OOM/driver reset: edge://crashes, Event Viewer Application, Windows Error Reporting, RAM/VRAM и GPU/renderer PID; не «чинить» отключением GPU без доказательства.

H6. Edge sleeping/discard: edge://discards и статусы discarded, но это не то же, что выход msedge.exe. Не открывать заново невосстановимый Temporary Chat, пока не согласована политика потери.

Нужны изолированный один-табовый прогон, затем 2 и 5 сессий, timestamps для browser_requested, browser_pid_observed, tab_created, temp_mode_verified, send_armed, message_saved, response_seen, tab_removed, process_exit. Диагностика без prompt/cookie/token.

## 5. Edge: высокоэффективный, но рабочий профиль

Использовать Start-Hermes-Edge-Temporary.ps1 в интерактивной Windows-сессии. По умолчанию CDP ВЫКЛЮЧЕН. Открывается строго https://chatgpt.com/?temporary-chat=true в отдельном профиле %LOCALAPPDATA%\Hermes-WebGPT\EdgeTemporaryProfile. Если в профиле уже есть процесс, новый таб не создаётся — существующую временную сессию нельзя незаметно заменить. Запуск подтверждает только start_requested, не TEMP_MODE_VERIFIED.

В edge://extensions устанавливать только проверенное Hermes расширение; дополнительные расширения пользовательского профиля не переносить. В edge://settings/system выключить Startup boost для этого отдельного использования, если фоновые msedge-процессы постоянно висят без задания. Выключение background mode допускается только после теста, что агент с работающей сессией не зависит от фонового режима. В edge://settings/system/managePerformance включить efficiency/energy saving выборочно и sleeping tabs для НЕАКТИВНЫХ вкладок.

Нельзя автоматически разрешать sleeping/discard активных temporary вкладок: фоновая генерация и JS timers могут прекратиться. В то же время исключение для всего домена chatgpt.com не даст спать и простоям. Баланс достигается прежде всего admission control, очередью новых задач, отсутствием непрерывных DOM-обходов и закрытием уже ЗАВЕРШЁННЫХ вкладок только по явно утверждённой политике.

Оставить graphics acceleration включённым (software rendering часто увеличивает CPU). Не использовать disable-gpu, no-sandbox, disable-site-isolation-trials, disable-web-security, disable-features с неаудированными элементами. Не блокировать сетевые ресурсы ChatGPT без анализа: auth, защита, streaming и загрузка файлов могут зависеть от них.

## 6. План управления нагрузкой и метрики

Сначала снять реальный baseline. Измерять отдельно:
- простой пустого Edge-профиля без Hermes (15-секундные срезы);
- одна временная вкладка: idle, ввод, активная генерация, завершённый ответ;
- две независимые временные вкладки: одна работает, другая ждёт;
- пять вкладок: 1 активная / 2 активные / 5 активных;
- штатный Edge и изолированный профиль на одной версии, той же машине, одинаковых запросах.

Для каждого кейса: 60 секунд прогрева, не менее пяти повторов, 15-секундные CPU-срезы; фиксировать median/p95 CPU(% machine), core equivalents, RSS/working set, количество процессов, потерянные ответы, успешные Send и задержку завершения. Автоматизированный sampler не читает страницу. Windows Edge Task Manager (Shift+Esc) проверяет, какой renderer/extension/utility действительно потребляет CPU. Отдельно edge://discards и edge://gpu.

Начальный критерий оптимизации, НЕ объявленный достигнутым:
- уменьшение baseline CPU в длительном idle на >=25% без изменения содержания/количества заданий;
- не ухудшить p95 ответа более чем на 5% для одинакового активного workload;
- 0 дублированных Send, 0 закрытых чужих вкладок, 0 «done» без долговечного подтверждения;
- 0 потерянных временных сессий в сценарии disconnect CDP; при полном crash — честная пауза, а не обещание восстановления.

Ветку не переводить в production по одной удачной synthetic fixture. Нужны измерения на реальном Windows/Edge и независимые проверки пользователя.

## 7. Что уже реализовано в этой ветке

- Start-Hermes-Edge-Temporary.ps1: отдельный профиль, точный URL, предотвращение незаметного второго запуска, отсутствие принудительного завершения, опциональный loopback CDP, метка неподтверждённого Temporary UI.
- Measure-Hermes-Edge-CPU.ps1: ограниченный read-only CPU sample без доступа к переписке. Группировка по процессному дереву приблизительная; новые PID внутри среза могут недосчитаться.
- core.js в web-companion + web-extension: кеш shadow-root discovery на 1500 мс, остаются свежими editor и кнопка.
- background_content.js в обоих каталогах: после подтверждённого saved-stage в report mode прекращаются повторные DOM-сканирования; потерянный ACK вызывает ограниченный повтор только stage, не Send.
- test_core.js и test_background_content.js: регрессии повторного сканирования и потерянного saved-stage ACK.
- test_edge_temporary_launcher.py: статические guard-тесты, не замена Windows E2E.

Отмечено принципиальное ограничение: новый launcher САМ по себе не превращает старое расширение в Hermes Temporary Chat transport. Existing /c/-bound report path пока не меняется, чтобы не сломать noReplay. Следующий шаг — отдельный versioned protocol для temporary ownership и UI-verification с фикстурами.

## 8. Матрица выпуска

T01: Launcher открывает нужный URL; оператор видит Temporary, не обычный чат.
T02: Закрытие Hermes worker/отсоединение CDP не закрывает Edge и active tab.
T03: Открытый ранее Edge не запускается повторно и не перезаписывает страницу.
T04: Незнакомая вкладка/новый логин/нет Temporary UI -> no Send.
T05: Одна отправка -> один user turn -> один matched response -> ACK.
T06: Worker перезапущен до intent -> безопасный новый claim по правилам.
T07: Worker перезапущен после armed -> uncertain_no_replay.
T08: Browser crash после Send -> no replay, ручная сверка.
T09: Lost report ACK -> retry evidence only, never Send.
T10: 1/2/5 параллельных вкладок, удержание окна и отсутствие дубликатов.
T11: Browser idle 10 минут, sleep/restore и сохранение временных вкладок.
T12: 30-минутный профиль CPU idle, 30-минутный активный профиль; все с медианой, p95, RAM.
T13: Реальный Telegram -> Hermes -> ChatGPT -> Hermes -> Telegram, оригинальный chat/topic, Bot API delivery ACK.
T14: После рестарта Windows login-profile не восстановлен из чужой сессии.
T15: Безопасное удаление/перезапуск без деструктивной команды в пользовательском Edge.

## 9. Нормативные и инженерные источники (проверены 22.11.2026)

- https://help.openai.com/en/articles/8914046-temporary-chat-in-chatgpt — Temporary Chat может не быть доступен из истории; personalized/unpersonalized выбирается до первого сообщения.
- https://playwright.dev/python/docs/api/class-browsertype — connect_over_cdp, launch_persistent_context; опасность закрытия context, созданного launch_persistent_context.
- https://playwright.dev/python/docs/api/class-browser — различие закрытия запущенного и подключённого браузера.
- https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle — MV3 worker может прекращаться после ~30 с idle; хранение state вне глобальных переменных.
- https://developer.chrome.com/docs/extensions/reference/api/tabs — active tabs, discard и tab lifecycle.
- https://learn.microsoft.com/en-us/troubleshoot/microsoft-edge/performance/edge-high-cpu-memory — Edge Task Manager, sleeping tabs, background apps, GPU.
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsblockedforurls — исключения для сна.
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/hardwareaccelerationmodeenabled — hardware acceleration.
- https://openai.com/policies/terms-of-use/ — отдельная проверка допустимости автоматического извлечения output; не считать Web UI заменой официального API.

## 10. Риск соблюдения условий сервиса

Текущие условия OpenAI запрещают программно извлекать данные или ответы из веб-сервиса. Этот план не утверждает, что автоматическая передача ответа через ChatGPT Web разрешена для production. Для полностью автоматизированного model transport предусмотрен отдельный путь через официальный API, без браузерного scraping. Разрешённые локальные действия: изолированный запуск, мониторинг собственных процессов, оптимизация расширения и UI-диагностика без извлечения ответов.

## 11. Release decision

CURRENT: DRAFT / NO-GO на production rollout Temporary Hermes transport. Причины: нет live validation Temporary UI, нет реального Windows PID/CPU baseline, не завершён новый session-ownership protocol, нет живого Telegram delivery. Все эти ограничения должны остаться в PR до подтверждения тестов; ничего не маскировать фразой «исправлено полностью».


## 12. Дополнительная оптимизация MV3 и результаты CI

После первого аудита найден более частый источник фоновой работы, чем `roots()`: `background.js` запускает полный `recoverOrphanedTabs()` на каждом сигнале тревоги (~30 секунд), сканирует все ChatGPT-вкладки и внедряет диагностический JS в каждую подходящую вкладку. В рабочей ветке оба зеркала `background.js` изменены:

- `RECOVERY_SCAN_GAP_MS=120000` — не чаще одного полного скана за 120 секунд, если часовое время идёт вперёд;
- `chatSmokeLastRecoveryScanAt` в `chrome.storage.session` переживает выгрузку и повторный старт MV3 service worker, в отличие от переменной в JS-памяти;
- сначала записывается отметка, потом запускается обход, чтобы соседние вызовы `poll()` не могли сразу повторить тяжёлую операцию;
- по окончании интервала полноценный скан повторяется; после холодного старта или очистки session storage он доступен немедленно;
- частота `poll()`, проверки `/v1/ack`, приём новых jobs и защита `noReplay` НЕ снижены;
- не изменены `chrome.tabs.remove` и существующая /c/-bound release policy для старого smoke режима. Это **ещё не** реализация требования «временный чат никогда не закрывать».

`web-companion/test_background.js` дополнен сценарием: первый скан происходит после старта; два ранних alarm не создают новое сканирование; при достижении 120000 мс восстановительный скан происходит снова. Существующий `test_recovery.js` продолжает проверять установление принадлежности и однократное закрытие только своего старого сохранённого диалога.

Проверка исходников GitHub Actions, Linux: `test_core.js`, `test_background_content.js`, `test_background.js`, `test_recovery.js` и `python -m unittest discover -s Hermes-WebGPT -p 'test_*.py'` — успешна. GitHub Actions, Windows: оригинальные PowerShell 5.1 parser checks для `Start-Hermes-Edge-Temporary.ps1` и `Measure-Hermes-Edge-CPU.ps1` — успешны. **Парсинг PowerShell не означает запуск Edge или замер CPU.** Подтверждение: https://github.com/Podtverzhdeno/Tunnel/actions/runs/45013026259

Важно: теоретическое уменьшение интенсивности полного обхода в 4 раза (120 секунд вместо 30) не эквивалентно уменьшению общего CPU в 4 раза. Неизвестна доля полного recovery в общей нагрузке. Для количественной оценки нужны Windows-срезы и Edge Task Manager.
