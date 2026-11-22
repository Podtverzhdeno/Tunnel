# Профиль Microsoft Edge для Hermes — безопасный performance-budget (Windows 10/11)

**Назначение:** один отдельный профиль браузера для Telegram/Hermes/WebGPT, а не ручная оптимизация основного пользовательского Edge. Не применять глобальные политики и изменения реестра без независимого измерения.

## 1. Почему «вырезать вообще всё» невозможно

ChatGPT Web требует JavaScript, сети, cookies/auth, CSP, Service Workers и UI/DOM для взаимодействия с интерфейсом. Отключение одного из этих механизмов рискует вызвать пустую страницу, разлогин, потерю streaming, ложное обнаружение ответа или бесконечную генерацию. Edge использует отдельные renderer, GPU и utility processes по конструкции; количество процессов `msedge.exe` не равно числу лишних программ.

Даже пустое расширение не может устранить CPU, который тратит React-интерфейс ChatGPT на генерацию/рендеринг. Настоящую долю нагрузки покажет **Shift+Esc** внутри Edge (Browser Task Manager). В первую очередь смотреть `Tab: ChatGPT`, `Extension: Hermes`, `GPU Process`, `Utility`, `Subframe`.

Модель для интерпретации срезов:

```text
CPU_total ≈ C_browser_base + C_extension + C_worker + C_gpu_driver
            + Σ_i C_chatgpt_renderer(tab_i, state_i)
CPU_marginal_N ≈ CPU(N identical tabs) − CPU(N−1 identical tabs)

Idle_10min, Generating_1, Generating_2, Generating_5 —
разные режимы, их нельзя смешивать.
```

`C` оценивать измерениями, не объявлять оценку теоретическим фактом.

## 2. Изоляция по принципу наименьших разрешений

Использовать `Start-Hermes-Edge-Temporary.ps1`: отдельный `%LOCALAPPDATA%\Hermes-WebGPT\EdgeTemporaryProfile`; запустить в **интерактивном** пользовательском сеансе, войти в ChatGPT вручную. Не переносить `Default`, `Login Data`, cookies, `User Data` и login session из личного профиля, не создавать приватный сервер, который расшаривает CDP по LAN.

Если CDP не требуется — оставить `-DebugPort 0` (значение по умолчанию). Если требуется эксперимент, использовать только loopback, случайный не занятый порт, отдельный аутентифицированный профиль и отдельные process visibility logs. CDP позволяет управлять браузером: treat localhost remote debugger as sensitive.

Обычный Edge пользователя должен остаться нетронутым, включая пароли, избранное, расширения, адресную строку и обычные чаты.

## 3. Что разрешено убрать в изолированном Hermes Edge

| Механизм | Начальная установка | Почему | Как проверить |
|---|---|---|---|
| Дополнительные расширения | Установить только проверенное Hermes-расширение | Уменьшение фоновых service workers | `edge://extensions`, Edge Task Manager |
| Startup Boost | Отключить для этого сценария, если idle не нужен | Не держать лишние процессы без окон | Закрыть все окна, проверить PID |
| Background extensions/apps | Для выделенного профиля по возможности не использовать; проверить жизненный цикл агента | Фоновые процессы могут продолжать потреблять CPU | No-window test, затем рабочая сессия |
| Sidebar / Copilot / shopping UI | Скрыть необязательные панели в настройках конкретного профиля | Убрать вспомогательный UI; ожидаемый CPU-выигрыш не измерен | Сравнение idle до/после |
| New tab page content | Не открывать новостную New Tab как постоянную anchor, использовать пустую при согласованной политике | Контент New Tab может потреблять CPU | Task Manager |
| Sleeping tabs | Включать только для действительно простаивающих, НЕ активных Hermes вкладок | Фоновое замораживание экономит ресурсы, но задерживает JS/стриминг | Проверка deferred/restore |
| Efficiency mode | Тестировать для idle, не применять вслепую к активной генерации | Может менять таймеры/задержки | p95 response + CPU |
| Hardware acceleration | Оставить включённым, сравнить только при подозрении на драйвер/renderer | Software rendering часто сильнее нагружает CPU | `edge://gpu`, renderer/driver |
| Legacy Hermes recovery scan | Вместо бессрочного частого обхода — event/queue admission и bounded reconcile | Уменьшает overhead расширения, не самой страницы | Trace alarms and injection count |

Названия меню Edge меняются; ориентироваться на `edge://settings/system`, `edge://settings/system/managePerformance`, `edge://extensions`, `edge://gpu`, `edge://discards`.

## 4. Что ЗАПРЕЩЕНО выключать ради CPU

**Не использовать:** `--no-sandbox`, `--disable-web-security`, `--disable-site-isolation-trials`, полное отключение SmartScreen, проверки сертификатов, браузерной sandbox политики, уязвимых опций безопасности. Не блокировать весь WebSocket/EventSource/служебный трафик ChatGPT. Не выключать JS/cookies/storage auth, если без этого Temporary UI не функционирует.

**Не рекомендовать как «ускорение»:** `--disable-gpu` (часто переносит композицию на CPU), `--disable-renderer-backgrounding` и `--disable-background-timer-throttling` (снимают полезные ограничения и нередко **повышают** CPU), бесконечные таймеры поддержания MV3 worker, `taskkill /F /IM msedge.exe` или `Stop-Process -Name msedge` (затрагивают личный Edge).

## 5. Оптимизация реального Hermes transport

Hermes 0.9.7 уже имеет одну активную запись `hermesRelayActive_v1` на расширение и защищённую очередь. Не вводить параллельные вкладки в том же профиле, пока нет независимых session leases, уникального document identity, соответствия временных чатов и экспериментальной CPU матрицы.

- Сначала довести idle extension сканирование до event-driven с fallback; не уменьшать частоту ACK и не ломать `noReplay`.
- Для будущего Temporary v2 держать вкладку от `PRE_SEND` до надёжного `RESULT_ACKED` (и при необходимости до доставки Telegram); на `UNCERTAIN` не закрывать.
- После доказанного terminal state и explicit owner policy допускается закрывать **только** owned task tab; если оно последнее в окне, использовать независимую лёгкую anchor-вкладку или продуманную window-retention policy. Не оставлять бесконечно большое число завершённых ChatGPT renderer.
- Сессии нужно различать по `session_id + profile + tabId + documentId`, а не по `url=/c/`. Temporary Chat может не иметь восстановимой истории.
- MutationObserver, даже без постоянного polling, тоже может потреблять CPU из-за streaming; `dom_reconcile_budget.cjs` коалесцирует события и имеет 5с fallback, но сначала необходим профайлинг на реальном DOM.

## 6. Как правильно сравнивать

1. Зафиксировать версию Edge, браузерный профиль, активные расширения, ноутбук/ПК и режим питания; не менять условия между вариантами.
2. Модель A: текущий браузер/расширение, модели B: изолированный Edge, C: Edge с экспериментальным v2 reconcile. Каждый прогон новый, исключить параллельный другой браузер.
3. Idle 10 минут; после прогрева прогнать идентичные сценарии 1, 2, 5 вкладок, не посылать вторично пользовательские запросы с неопределённым Send. Для исследовательского прогона использовать synthetic HTML fixture.
4. Снимать p50/p95 CPU (измерение процента всей машины и core-equivalents), p50/p95 working set, число процессов, latency до готового ответа и число ошибочных/дублирующих Send. `WorkingSet64` суммирует пересекающиеся области — не выдавать это за приватную физическую RAM.
5. Для p95 минимум 20 независимых стабильных срезов на режим, лучше повторить 3+ полноценных сеанса. Отмечать `insufficient_stable_samples` и нагрузку самого инструмента наблюдения.
6. При любом несоответствии ответа request_id, аккаунта, Temporary mode, document epoch или сохранённой записи — отменить эксперимент, не выполнять автоповтор.

**Конкретная цель, пока не достижение:** снизить idle CPU extension ≥25% относительно измеренного baseline, сохранить нулевое число дубликатов/закрытых чужих вкладок и не увеличить p95 end-to-end больше чем на 5% в одинаковой нагрузке. Для настоящей активной вкладки CPU существенно может не уменьшиться, если нагрузка внутри ChatGPT Web renderer.

## 7. Авторитетные источники

- Microsoft Edge CPU/memory troubleshooting: https://learn.microsoft.com/en-us/troubleshoot/microsoft-edge/performance/edge-high-cpu-memory
- Edge Sleeping Tabs: https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsenabled
- Chromium MV3 worker lifetime: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle
- Playwright BrowserType CDP and persistent context: https://playwright.dev/python/docs/api/class-browsertype
- OpenAI Temporary Chat: https://help.openai.com/en/articles/8914046-temporary-chat-in-chatgpt
