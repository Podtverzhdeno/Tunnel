# Hermes-WebGPT — SDD: атомарная диагностика, журнал причин остановки и контракт интеграции десятков агентов

**Дата:** 2026-11-22. **Статус:** DRAFT, исходники/офлайн-тесты; НЕ production deploy, НЕ постоянно работающий локальный монитор. **База:** Podtverzhdeno/Tunnel main `350ea33080bc35245aa6741f85fa926cc8a8fcaf`.

Этот документ — дополнение, а не конкурент для PR #13 (read-only incident sampler/triage) и PR #14 (sticky blocker/recovery SDD). Результаты отнесены строго к наблюдаемому коду/ревизиям. Никакой агент не может считать недоказанную остановку успешным или безопасно повторяемым действием.

## 1. Реально обнаруженное устройство и доверенные границы

`Telegram intent → Hermes Gateway → Hermes-WebGPT/relay.py (SQLite WAL / turn_bindings / route / lease) → один выбранный Web Companion либо CDP → ChatGPT Web → bound result/tool call → Hermes → Gateway Telegram outbound ACK`.

- `relay.py` контролирует идентификатор запроса, транспорт и аренду; Queue.claim() сейчас допускает только одну активную заявку в `claimed/intent/saved` глобально. 25 вкладок браузера — НЕ 25 одновременных model turns.
- `web-companion/bridge.py` содержит отдельный browser bridge и ограниченный последние 60 событий diagnostics tail. PR #14 сохраняет самый ранний блокирующий сигнал и не позволяет испорченному журналу выглядеть пустым; full durable ledger и двухфазное подтверждение Send — ещё не готовы.
- `web-companion/background_content.js` в проверенном main буквально выполняет `button.click(); // exactly once`, **потом** `await stage("send_clicked")`. При crash в этом интервале нет гарантированной долговременной записи, отличающей выполненный Send от невыполненного. Это P0 архитектурный разрыв, НЕ доказательство, что в реальной сессии произошёл дубль.
- `background.js` использует MV3 `chrome.storage.session`, асинхронную телеметрию и управление owned tabs; после restart/service worker suspend отсутствие callback ≠ отсутствие Send.
- `monitoring/queue_monitor.py` является существующим read-only SQLite state reader; PR #13 создаёт read-only causal triage. Не создавать второй authoritative queue.
- Публичный `manifest.json` v0.6.6 и исторически описанная локальная v0.9.7 — не одно и то же. Установленную сборку, PID, browser profile, runtime SHA и реальный Telegram ACK на текущем Local_WorkSpace проверить не удалось. Отдельное рабочее пространство Hermes недоступно. Никакие незаверенные локальные правки не объявляются внесёнными.

## 2. Обязательный release invariant: два браузерных зеркала

В текущем Git существуют `chat-launch-smoke-desktop-test/web-companion/` (авторские источники) и `chat-launch-smoke-desktop-test/web-extension/` (копия для установки). Общий публичный набор **строго семь файлов**:

`manifest.json`, `core.js`, `content.js`, `background.js`, `background_content.js`, `popup.html`, `popup.js`.

У этих семи путей Git blob SHA в обоих каталогах **совпадают** на проверенном main; это не свидетельствует о текущей локальной установке. В `web-companion/build_clean_extension.py` явно предусмотрен приватный `bridge-config.json` с bearer, который не должен попадать в Git, журналы или отчёты. Не сканировать его содержимое.

PR #10 и #11 оба изменяют `background.js`, `background_content.js`, `popup.html`, `popup.js` в обоих зеркалах. Текстовый git merge, даже если проходит, не проверяет корректности двух стратегий освобождения вкладок и ограничений параллельности. Назначить **одного** владельца этой path group, объединить изменения на отдельном интеграционном exact SHA и проверить **зеркала после merge**.

### Реализован отдельный read-only checker

`Hermes-WebGPT/monitoring/extension_mirror_guard.py`:
1. читает только семь allowlisted открытых файлов из каждой папки, не трогая приватные настройки;
2. блокирует missing file, symlink в любом компоненте пути, файл более 5 MiB, любые несовпадающие байты;
3. привязывает контракт к MV3 v0.6.6; `permissions=[alarms,tabs,storage,scripting]`; `host_permissions=[http://127.0.0.1:18763/*,https://chatgpt.com/*]`; единственный content script на chatgpt.com и ожидаемый service worker. Расширение прав/других сайтов требует явно ревьюируемого изменения спецификации;
4. создаёт JSON со SHA-256 исходников, проверенным числом файлов и необязательным exact 40-hex revision, который сверяется с чистым HEAD и tracked file set, реально сверенным с clean git HEAD и tracked files; **не содержит текста промптов, токенов, DOM, URL с query, SQLite, пользовательских путей**;
5. `source_pair_verified`/exit 0 относится только к файлам, `blocked`/exit 2 — нарушение security contract или зеркало, `evidence_unknown`/exit 3 — неполное чтение. Fail-closed: отсутствие файлов не равно OK;
6. атомарно заменяет только разрешённый отчёт `mirror-report.json` или `webgpt-mirror-report.json` внутри checkout, запрещает symlink, выход за корень, запись в папки browser/secret и fsync'ит файл (не делает вид, что транзакционно подтверждает внешний Send); никогда не создаёт новую заявку и не даёт права на retry.

`test_extension_mirror_guard.py` проверяет 45 сценариев на синтетических данных: mismatch по JS и manifest, более широкие разрешения/хосты, неверный MV3, missing path, symlink file/parent, oversize, приватный config, отсутствие ложного runtime proof, корректность JSON и выходов. Офлайн-результат 45/45 PASS в изолированном Python, не E2E.

`.github/workflows/hermes-webgpt-mirror-integrity.yml` запускает эти проверки при PR/push main и дважды в день **только после появления workflow в main**. Не считать GitHub Actions заменой проверки живого расширения. Отчёт — локальная безопасная проекция evidence, не криптографическая подпись владельца.

Запуск из Git-корня:
```sh
python -m unittest discover -s Hermes-WebGPT/monitoring -p 'test_extension_mirror_guard.py' -v
python Hermes-WebGPT/monitoring/extension_mirror_guard.py --root . --report mirror-report.json
```

## 3. Коллизии между одновременно работающими агентами

| PR | Изменяемая зона | Риск | Действие integration owner |
| --- | --- | --- | --- |
| #7 | MCP/Playwright/CDP fixture и тесты | Пересечение с реализацией #8 | Сверить протокол request_id, ownership и transport route; fixture proof отдельно от live |
| #8 | Telegram → Relay → MCP stdio → CDP | **Stacked на #6**, меняет часть тех же CDP-файлов, что #7 | Не вносить в main независимо от родителя; выбрать одного CDP owner, тестировать один исполнитель на один turn |
| #10 | Edge Temporary Chat/DOM budget/lifecycle | Общие файлы с #11, разная семантика /c URL | Не склеивать handlers; решить, подходит ли Temporary Chat для сохранённого URL и report |
| #11 | Edge CPU/keepalive/worker budget | Общие mirror JS с #10 | Зафиксировать cap и admission; сравнивать 2/6/25 tabs, не угадывать CPU |
| #12 | Исследование SIWC/Codex/App Server | Альтернативные, несовместимые transport модели | Не трактовать исследование как авторизованный и deployed fallback |
| #13 | Incident sampler/triage | Схема причин пересекается с #14 | Read-only observer; не вводить второй truth ledger |
| #14 | Sticky recovery evidence, bridge, SDD | Защищает truncated-tail recovery, не исправляет click-before-intent | Ревьюировать журналы и repair отдельно, использовать durable receipt |

**Глобальное правило:** один integration owner на shared extension mirror; второй на Relay state machine; третий на bridge recovery; A4 наблюдатель, A5 chaos QA, A6 host permissions и A7 независимый release reviewer. Agent ID не равен credential. Изменения должны иметь allowlisted paths, base SHA, обновляемый ownership lease/fence и compare-and-swap. Смена HEAD немедленно обесценивает старый CI proof. Review не может быть самоподписанным, а SDD не заменяет enforced P085 approval. Никаких force push или тихих перезаписей веток других агентов.

## 4. Предлагаемый контракт причинной трассировки

Предпроектный event envelope (не утверждаем live): `schema_version, event_id, task_id_HMAC, request_id_HMAC, turn_id_HMAC, parent_event_id, attempt_id, lease_epoch, agent_id, process_boot_id, transport, source_seq, source_sha, step, event_kind, reason_code, evidence_class, observed_at, received_at, action_key, provider_receipt_ref`.

Нужны independent server-received time и source-local sequence; client timestamp может опаздывать или врать. Heartbeat ≠ progress; UI stage ≠ durable Relay ACK; Relay done ≠ Telegram delivered. Event collector валидирует producer identity и realm, size limits, allowlist, дедуп по event_id + body digest, сохраняет вход только после durable COMMIT. При пропуске sequence ставит `TRACE_INCOMPLETE`, не «healthy». Логи metadata only; один источник истины у Relay для состояния request, у Gateway для outbound Telegram receipt, у host/P085 для прав, у GitHub exact commit/check для сборок.

Целевая проверяемая цепочка:
```text
gateway.telegram_intent_authenticated
 → relay.accepted(request_key,turn_id,transport)
 → relay.claimed(lease_epoch,worker_id)
 → browser.owner_scope_verified(tab/page/profile/document)
 → send.intent_reserved_durable(action_hash)
 → send.intent_ack_received_by_browser
 → browser.click_attempted_once
 → chatgpt.user_turn_saved_read_back
 → chatgpt.assistant_reply_bound(request_id)
 → hermes.tool_schema_and_scope_verified
 → gateway.telegram_outbound_ack(chat/topic/message_id)
 → independently_verified_complete
```
**Сейчас кодовый `send_clicked` после клика не обеспечивает `send.intent_reserved_durable`.** Не объявлять полный процесс атомарным. SQLite COMMIT невозможно поместить в одну ACID-транзакцию с веб-ChatGPT click или Telegram API. Реалистичный контроль: durable intent перед возможным внешним действием + at-most-one unsafe attempt на fenced key + read-back/reconciliation при неизвестности. Blind retry non-idempotent Send запрещён.

### Наблюдение и safe recovery
| Reason/симптом | Чем различить гипотезы | Безопасная реакция |
| --- | --- | --- |
| PRE_SEND_REJECTED | Достоверный refusal до резервирования/клика | Исправить preflight; новые действия только по policy/лимиту |
| POST_SEND_UNKNOWN | Есть/мог быть click, нет независимого receipt | Сохранить вкладку, не нажимать второй раз |
| SENT_NO_REPLY | Подтверждён user turn, но нет bound assistant response | Read-back той же сессии; не менять request ID |
| REPLY_UNBOUND | ID/turn/account mismatch | Reject result/tool calls, reconciliation |
| RELAY_DONE_TELEGRAM_UNKNOWN | Relay completion без outbound ACK | Gateway read-back, не подменять success |
| MV3_SUSPENDED | background/service worker не отвечает | Проверять Relay/bridge независимо; UI offline ≠ job failure |
| TAB_LEASE_DISPUTED | Сменился owner/profile/document generation | Запрет stale writes; independent read-only reconciliation |
| AUTH_CHANGED | login/challenge/profile conflict | Human action, никакого cookie copying |
| EDGE_WINDOW_DISAPPEARED | final tab removed ИЛИ native idle cleanup ИЛИ CDP close ИЛИ OS | Windows parent/child PID, tab count, browser lifecycle logs |
| PROGRESS_STALE | heartbeat есть, evidence_seq не меняется | Инспекция текущего step, лимит ожидания |
| QUOTA_429 | Provider quota/Retry-After | Shared DAG budget, backoff только safe reads |
| APPROVAL_EXPIRED_OR_DENIED | Exact action digest TTL и host reviewer | Не перепривязывать approval к другому действию |
| OBSERVER_OFFLINE | Отсутствие событий наблюдателя | `evidence_unknown` с покрытием и cursor, не all-clear |
| CI_STALE | Checked SHA отличается от текущего HEAD | Перезапуск CI на exact SHA |
| REPLAY_DUPLICATE | Повтор source sequence/event ID, несовпавший digest | Reject/incident; не второй send |

У каждого incident три отдельных слоя: **(A)** факт `observed_symptom`, **(B)** `root_cause_candidates[]` с различающими проверками и источником, **(C)** действие `safe_next_action` только при наличии policy authority. Не присваивать гипотезе категорию «подтверждено» по одному таймауту.

## 5. Программа испытаний, выходных доказательств и конкурентности

Release gate для настоящей GPT-интеграции включает: mirror checker **на общем merge SHA**, позитивную/негативную матрицу в браузере с отдельным профилем, baseline vs exact-HEAD Python/Node suites, acceptance of explicit Telegram reply, current owner/lease/fence and P085 approval. Ключевые chaos fixtures: click-success/ACK-loss; crash between click and stage; browser process vs MV3 death; last-tab close; ambiguous CDP owner vs extension; resume after change of account; stale worker send; old blocking event pushed out of 60-event tail; corrupt diagnostics; 429; re-ordered event sequence; malicious DOM content as untrusted data; Telegram ACK after Relay done.

Проверяемая количественная модель для 10/30/50 логических агентов (не считать одновременно работающими вкладками): количество необоснованных duplicate unsafe effects = 0 в контролируемом fixture; количество `unknown` или `blocked` **не** должно автоматически снижаться под давлением watchdog; общая доля tasks с отсутствующими evidence — отдельная метрика. CPU/RAM, p50/p95 inference, recovery, очереди, rate limiting и тарифы измерять на реальном Windows хосте до изменения cap. Родительский DAG ограничивает общий cost, provider QPS, число активных страниц, max fanout/depth, retry of safe reads, и запрещает бессодержательный бесконечный цикл «продолжай».

Варианты (экспертные оценки, не measured): веса safety 35%, observability 25%, multi-agent 20%, migration ease 10%, rollback 10%, шкала 1–5:
| Идея | Safety | Obs | Multi | Ease | Rollback | /100 | Итог |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Durable intent-before-click + receipt reconciliation | 5 | 5 | 5 | 2 | 3 | **90** | P0 целевое изменение после host canary |
| Единый mirror contract и path owner | 5 | 4 | 5 | 5 | 5 | **95** | Реализовано как isolated draft |
| Read-only triage + causal provenance | 5 | 5 | 4 | 5 | 5 | **96** | PR #13/#14 совместить по event schema |
| 25 постоянно активных ChatGPT SPA tabs | 2 | 2 | 2 | 3 | 4 | **46** | Необоснованно без CPU benchmark |
| Blind retry неизвестного Send | 1 | 1 | 1 | 4 | 1 | **26** | Жёсткий запрет |

Оценка не отменяет hard veto при слепом повторном Send, stale approval/fence, потерянной идемпотентности, скопированном ChatGPT профиле, несанкционированном CDP порте, выданном без proof success. Один и тот же агент не может одновременно создавать эффект, подтверждать результат и снимать контроль.

## 6. Handoff другому агенту и аудит доказательств

Work item должен быть неизменным контрактом: `task_id, parent_id, owner_id, role, base_sha, allowlisted_paths, read_only_paths, reviewer_id, lease_epoch, approval_scope, dependency_graph, budget, max_children, failure_hypothesis, distinguishing_test, exact_sha_ci, artifacts_digests, pending_unknown_effects, safe_recovery_step`. Worker после утраты сессии начинает с этого snapshot, а не реконструирует скрытые chain-of-thought. Для синхронизации — append-only versioned manifest, а не длинная переписка.

**Подтверждено на 2026-11-22:** тесты проверяют также невозможность перезаписать `bridge-config.json`, исходники JS, `.git` или внешний путь при записи JSON-отчёта.  локально проверено, что Git HEAD не может быть подменён валидно выглядящим SHA; незакоммиченные изменения в двух зеркалах делают результат недостоверным. Исходный репозиторий, PR #7/#8/#10/#11/#12/#13/#14 и file overlaps; mirror blob SHA семи файлов main; реальный click-before-stage порядок; 34 synthetic tests локально; отдельный read-only checker и workflow. **Не подтверждено:** загруженная локальная версия, запуск Edge/Web Companion, real browser/Telegram effects, сбой конкретной пользовательской сессии, стабильность объединённых PR, безопасность production. После каждого последующего изменения добавлять конкретный source SHA, проверку, реальную CI job, гипотезу, proof и owner, не отчёт «всё исправлено» без измерения.

Основные связанные документы: [PR #13](https://github.com/Podtverzhdeno/Tunnel/pull/13), [PR #14](https://github.com/Podtverzhdeno/Tunnel/pull/14), [PR #10](https://github.com/Podtverzhdeno/Tunnel/pull/10), [PR #11](https://github.com/Podtverzhdeno/Tunnel/pull/11), [PR #8](https://github.com/Podtverzhdeno/Tunnel/pull/8). Не брать права из SDD и не активировать монитор, пока не подтверждён permission/host runtime.


## 7. Внешняя верификация рисков — SQLite, Chrome MV3, GitHub Actions и OTel (2026-11-22)

Это проверка фактов по **официальной документации**, а не наблюдение пользовательской Windows-машины или браузера. Не путать отдельные причины и уровни доказательств.

### 7.1 SQLite: наблюдаемая ошибка и отдельный редкий риск целостности

В соседнем PR #13 (head `d66a98ee908590bf529f9e6cafa7d0387925aad0`, GitHub Actions run 45016143604, Windows Python 3.13) подтверждён сбой: `test_samplers_serialize_without_losing_baseline_or_log` запускает 24 конкурирующих `run_cycle`; один `incident_sampler.py` упал на `PRAGMA journal_mode=WAL` с `sqlite3.OperationalError: database is locked`. Ubuntu и Windows3.11 тесты в этом запуске прошли, но это не отменяет Windows3.13 ошибку. Из исходников видно: настройка WAL и `CREATE TABLE IF NOT EXISTS` выполняются **до** `BEGIN IMMEDIATE` и повторяются в каждом цикле. Сериализация внутри транзакции не защищает инициализацию.

SQLite документирует, что `journal_mode=WAL` сохраняется для последующих соединений. Гипотеза исправления: вынести WAL/DDL в идемпотентную сериализованную инициализацию; обычный цикл — только read-only sampling плюс атомарное `BEGIN IMMEDIATE` обновление incident/baseline. Отдельно классифицировать `journal_schema_lock_busy` и `relay_send_unknown`: локальный безопасный повтор открытия SQLite БД **не разрешает** повторять ChatGPT Send. Многократно проверять Windows3.13 на 24–50 наблюдателях с сохранением количества/порядка записей после restart. Источник: https://www.sqlite.org/wal.html#persistence_of_wal_mode

**Другой, очень редкий риск:** официальная SQLite WAL Documentation `11 описывает WAL-reset race с риском порчи данных у нескольких соединений на одной БД в разных потоках или процессах при конкурентной записи/checkpoint. Документация указывает вероятно затронутые версии `3.7.0..3.51.2`, исправление в `3.51.3+`, backports `3.44.6` и `3.50.7`. Разработчики подчёркивают низкую вероятность: нельзя приписывать зафиксированное `database is locked` этому редкому дефекту и нельзя объявлять пользовательскую БД повреждённой. Но перед 50-агентным запуском проверить `sqlite3.sqlite_version` и provenance SQLite **каждого реального runtime** (Relay, sampler, Windows Python, CI и упакованный binary), включая vendor backports. Для нескольких конкурентных writer-процессов выбрать исправленную сборку и тестировать commit/checkpoint с реальным файловым хранилищем. Источник: https://www.sqlite.org/wal.html#the_wal_reset_bug

**Долговременность:** `-wal` является частью состояния БД. Одиночная копия `*.db` при активном writer ненадёжна; использовать SQLite backup API или согласованный checkpoint/quiescence, не удалять `-wal/-shm` вручную. Проверять `PRAGMA synchronous`: при WAL `FULL` синхронизирует каждый commit, `NORMAL` допускает потерю последних операций после power loss. Не размещать SQLite WAL на общем SMB/NFS для разных машин. Источники: https://www.sqlite.org/wal.html#the_wal_file ; https://www.sqlite.org/wal.html#performance_considerations ; https://www.sqlite.org/wal.html#overview

### 7.2 Chrome MV3: service worker lifecycle не доказывает состояние модели

Chrome MV3 worker штатно завершается после **30 секунд неактивности**; глобальные JS переменные при остановке теряются, а другие виды запросов имеют отдельные лимиты. Это не самодиагностируемый сбой ChatGPT. Recovery должен читать durable Relay/bridge owner binding, проверять уже отправленный user turn и сверять source_seq + последний внешний эффект **до нового действия**. Не удерживать worker бессмысленно активным и не трактовать потерю heartbeat как разрешение нового Send. Источник: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle

### 7.3 GitHub PR: различать два SHA

По GitHub Docs `GITHUB_SHA` для workflow события `pull_request` указывает обычно **синтетический merge commit** `refs/pull/N/merge`, а `github.event.pull_request.head.sha` — последний реальный коммит PR. `actions/checkout` по умолчанию проверяет объединённое состояние. Наш `extension_mirror_guard --revision "$GITHUB_SHA"` сравнивает с фактическим `git HEAD` checkout, то есть проверяемого merge ref, что корректно для интеграции. В дальнейших отчётах нужно явно иметь `pr_head_sha` (триггер), `tested_git_sha` (реальный checkout), `base_sha`; нельзя путать metadata `workflow_run.head_sha` и действительно запущенный checkout SHA. При изменении любого входа перепроверка обязательна. Источник: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request

### 7.4 Наблюдение должно иметь один authoritative stream

OpenTelemetry GenAI Semantic Conventions плюс Weaver validator — **кандидат** для проверки формата уже существующего event stream, а не новый авторитетный scheduler или queue. Статья OTel (ноябрь 2026) использует Weaver для missing_attribute, type_mismatch и policy, отчёт с violation блокирует acceptance. Можно применить при изолированном протоколе без передачи prompt, DOM, cookie, personal messages, raw tool args и bearer. Сначала измерить cardinality, стоимость и scoped consent. Источник: https://opentelemetry.io/blog/2026/agent-harness-genai-instrumentation/

### 7.5 Приоритеты подтверждения

1. Узнать фактические SQLite версии и WAL/synchronous на Windows и CI, устранить воспроизведённый #13 lock через однократную контролируемую инициализацию.
2. Назначить одного владельца пересекающихся PR #10/#11, проверить оба зеркала на combined merge SHA, затем интеграционные сценарии.
3. Спроектировать и после независимого security/host approval выполнить durable pre-click ACK, fenced effect identity, at-most-one unsafe attempt, read-back; не ускорять 25 вкладок прежде безопасности.
4. Тестировать MV3 stop/restart, потерю ACK после click, аккаунт/вкладку и независимый Telegram receipt.
5. Только после этого — проверку event schema, p95, нагрузку 10/30/50 логических агентов и поэтапный supervised rollout.

Ни 45 mirror-тестов, ни этот SQLite source audit **не подтверждают** состояние текущей Windows-инсталляции, отправку ChatGPT или доставку Telegram. Уровень доказательства: `SOURCE_VERIFIED/CI_VERIFIED`; `RUNTIME/USER_EFFECT_UNKNOWN`.


## 8. Критический proof gap: почему простой reorder \`send_clicked\` не исправляет атомарность

Подтверждено чтением **текущего main, не live runtime**:

1. `chat-launch-smoke-desktop-test/web-companion/background_content.js`: функция `stage(value)` делает `await chrome.runtime.sendMessage(...)` внутри `try/catch`, но **игнорирует возвращённый `ok:false` и глотает exception**. Эта функция возвращает управление без признаком verified/durable ACK. Строки 110–111 выполняют `button.click(); await stage("send_clicked")` — внешний побочный эффект предшествует отметке.
2. `chat-launch-smoke-desktop-test/web-companion/background.js`: обработчик `chat-smoke-stage` пишет `chrome.storage.local.set(chatSmokeLast)`, затем вызывает `telemetry()`. Внутри `telemetry()` ошибка POST к bridge **глотается**, только пишется console.warn; обработчик всё равно отвечает `sendResponse({ok:true})`. Поэтому даже если перенести вызов `await stage("send_clicked")` перед `button.click()`, локальное `ok:true` **не означает**, что bridge/Relay durable committed intent.
3. `chat-launch-smoke-desktop-test/web-companion/bridge.py`: `/v1/event` хранит ограниченный tail диагностических стадий, без action-level запрета повторного Send. PR #14 сохраняет самый ранний blocker после 60 записей и добавляет file fsync, но не вводит полномочия на pre-click Send, не связывает sender principal с action digest и не добавляет durable prepare/commit протокол.

**Следствие:** простой reorder даёт **ложное ощущение безопасности**. Эксперимент с принудительным выпадением POST event при нормальном ответе из background обязан приводить к **NO_SEND**, а не к click.

### Требуемый контракт для нового безопасного endpoint (только спецификация)

Рекомендуется одноразовый write reservation с серверным владельцем вместо использования общего диагностического `/v1/event`:

`content script → background → trusted bridge or Relay (`prepare_send`) → durable SQL COMMIT + unique(action_key,attempt_epoch) → explicit ack with job_id, attempt_id, action_digest, tab_id, profile_id, origin, doc_generation, lease_epoch, expiry → check matching ack → button.click() once → record attempt/outcome → independent read-back`.

- `action_digest` binds bounded canonical prompt digest **inside trusted layer**, not raw prompt in log; use HMAC where IDs could leak user metadata. Server stores action reservation BEFORE approving click, and server-side unique constraint prevents second grant by other worker even after restart.
- Ack must indicate **durable commitment** and exact binding; generic `HTTP 200`, local `chrome.storage` write, response with missing fields, stale fence, changed tab/doc/login, and a retry from another agent must be rejected as `INTENT_ACK_UNTRUSTED`. Diagnostic message `send_clicked` is a post-action observation, not auth.
- If the service worker dies between receipt and browser click, server has reservation with `OUTCOME_UNKNOWN` or `NOT_ATTEMPTED_UNPROVEN`; never auto-reserve a second click without server-side reconciliation and independent profile/tab read-back. If app can't reliably know browser click outcome, represent this uncertainty.
- Only one logically authoritative reservation owner; isolate from #10/#11 file edits and #14 bridge work. Addition of a new endpoint requires its own security review, scoped host approval, proof-of-failure tests and rollback. Do **not** permit an observer to invoke it.
- Power-loss claims need SQLite `synchronous=FULL` / durable storage tests and, where using file rename on supported filesystems, review parent-directory durability. Atomic process visibility does **not** necessarily mean power-loss durability.
- Acceptance fixtures: (a) dropped bridge POST after service worker local state write, (b) `ok:false` response after pre-click stage, (c) browser closes before click, (d) click occurs but completion ACK lost, (e) reconnect with stale worker lease, (f) two workers same action_key, (g) changed Chrome profile/account/tab/document, (h) event tail rolls over 60, (i) corrupt/partial observer log, (j) Telegram output ACK delayed. Any non-idempotent Send second execution fails the test.

**Source provenance:** `Podtverzhdeno/Tunnel` main baseline `350ea33080bc35245aa6741f85fa926cc8a8fcaf`, checked ``background_content.js:17–24, 109–111`, ``background.js:129–139, 607–628`, ``bridge.py:395–450`. PR #10/#11/#14 may modify this; repeat review on final integrated SHA before trusting any fix.


## 9. Взаимодействие lock, HTTP ingress и наблюдения за зависанием

Проверка исходников main и PR #14 (без реального load test) выявляет риск **неограниченного pre-auth ingress**, который отличается от лимита одновременно живых агентских заявок:

- `Hermes-WebGPT/relay.py`: базовый `ThreadingHTTPServer` порождает HTTP handler thread до проверки авторизации и до проверки `max_http_waiters` в PR #15. Ограничение attached waiters **не эквивалентно** ограничению новых TCP clients / slow-header connections.
- `chat-launch-smoke-desktop-test/web-companion/bridge.py`: тот же `ThreadingHTTPServer` привязан к loopback `127.0.0.1:18763` и проверяет Host/`X-Chat-Smoke-Key` в `do_GET/do_POST`. Loopback существенно снижает внешнюю поверхность угроз, но множество локальных клиентов, не завершивших заголовки, может расходовать потоки **до** этой проверки.
- Все операции claim/telemetry/ACK могут ждать глобальный `LOCK`. PR #14 добавляет `fsync` записи diagnostic JSON внутри глобальной блокировки, сохраняя noReplay-свидетельство, но увеличивает критический участок при больших 10/25/50 agent fanout. Без измерения нельзя утверждать ни наличие bottleneck, ни безопасный throughput.

**Новый набор отдельных reason codes**: `HTTP_ACCEPT_QUEUE_FULL`, `HTTP_HANDLER_CAPACITY`, `BRIDGE_LOCK_WAIT`, `DIAGNOSTIC_FSYNC_SLOW`, `HTTP_WAITER_CAPACITY`, `BROWSER_CLAIM_CAPACITY`. Это взаимоисключающие диагностические слои, а не синоним `LLM_STALLED`. К каждому нужны количественные latency/active count + denied count с фиксированным sampling interval, без user prompt/body/header.

**Фальсифицируемые тесты:** в изолированном Windows/Ubuntu инстансе открыть 100–200 медленных соединений без обязательных заголовков, затем выполнить один нормальный аутентифицированный health/ACK; доказать bounded handler threads, bounded memory и отсутствие starvation. Отдельно нагрузить 25 одновременных разрешённых, но metadata-only `/v1/event` с fsync и измерить lock wait p50/p95/max, ошибочные 503/429, потерю событий и сохранность самого раннего blocker. Разнести лимит приёма/обработки до создания потока и bounded journal writer, не держать один глобальный lock на дорогом fsync без анализа event ordering. Измерять CPU/RAM Windows Edge отдельно от очередей Relay.

**План изменений:** назначить одному интегратору `bridge.py`, другому `relay.py`; не вводить HTTP web server под эгидой наблюдателя. Защищённый режим с admission cap и ограниченным временем чтения не должен отвергать уже зарегистрированный completion receipt/ACK или стирать состояние неопределённого Send. До подтверждения slow-client и fsync load tests этот риск остаётся `SOURCE_LEVEL_RISK_NOT_PROVEN_IN_RUNTIME`.


## 10. Происхождение событий: какой из десятков агентов сообщил stage?

Сравнение main `bridge.py:180–194`, `bridge.py:395–450`, `background.js:129–139` и `recovery_candidate():48–80`:
- Auth HTTP проверяет общий bearer `X-Chat-Smoke-Key` и loopback Host; произвольный `Origin` не даёт доступ без ключа, но допустимым browser-origin является любой корректно оформленный `chrome-extension://...`, а не закреплённый installer extension ID.
- После такого auth endpoint `/v1/event` принимает `{id,stage,reason}`; наличие job в QUEUE/ACTIVE/DONE — минимальный тест, но событийная запись не содержит trusted producer, worker/session/tab, attempt sequence или fence. Даты и порядок элементов 60-event tail — только порядок серверного наблюдения.
- `recovery_candidate` проверяет наличие `send_clicked` и более позднего `message_saved` и блокирующих reasons; это разумный консервативный фильтр **если события достоверно принадлежат одной попытке**. При сотнях agent processes с переиспользуемым bridge credential событие, поданное другой авторизованной копией, может нарушить provenance. Нет доказательства, что в реальной установке кто-то подделал данные.

**Целевой contract (не внедрён):**
1. Bridge ограничивает principal на конкретные `agent_id, profile_id, browser_extension_install_id` и выдаёт краткоживущий capability для `job_id, attempt_id, worker_id, tab_id, document_generation, lease_epoch, allowed event kinds`. Credential не должен попадать в промпты, DOM или общие GitHub/Obsidian файлы.
2. Тело события включает `schema_version, event_uuid, producer_seq, action_key, parent_event_id, source_timestamp, producer_boot_id`; доверенный bridge добавляет `received_at`, auth principal и runtime SHA. Пара `event_uuid + body_digest` задаёт идемпотентную дедупликацию; mismatch того же event ID — инцидент, не новый state.
3. `stage=send_clicked` не является разрешением действовать, доказательством server commit или independent provider receipt. Только `prepare_send` grants, описанный в `8, может разблокировать одно действие; duplicate/old lease/document → reject с неизменным incident marker.
4. Доказательство session/turn/order нельзя выводить только по wall clock. Восстановление требует source-local sequence, parent ID, source version и владение документом. Две несовместимые истории должны приводить к `TRACE_PROVENANCE_DISPUTED` и `EFFECT_OUTCOME_UNKNOWN`; вне trusted bridge нельзя снимать этот статус.
5. Для backward compatibility legacy clients сохраняется отдельное поле `trust=legacy_unverified`: они могут передавать диагностический heartbeat, но не создают trusted provenance и не разрешают мутирующие операции. Canary миграция должна обеспечивать неутечку private profile data.

**Тесты происхождения:** одновременные агенты A/B с общим историческим ключом и разными job scopes; ответ event B к job A должен быть отвергнут новыми capability rules; duplicate UUID same payload → один durable event; duplicate UUID changed payload → security incident; out-of-order `producer_seq` → trace_gap; reboot sender без new boot id → stale; expired approval/fence → rejection. Это **предложение безопасной будущей миграции**, не утверждение, что текущая реализация уже защищена такими механизмами.


## 11. Устанавливаемая упаковка и merge queue для десятков разработчиков

После строгой проверки семи зеркальных файлов обнаружен ещё один класс риска: в папку `web-extension/` могли добавить **восьмой исполняемый файл/ресурс**, который не попал бы в hash-compare. На main там ровно семь публичных файлов. Гейт теперь не только сравнивает содержимое, но и требует, чтобы **весь список элементов установочного каталога** ограничивался утверждёнными семью файлами. Единственное допустимое исключение — локальный `bridge-config.json`, который не открывается и не сериализуется в отчёт; symlink под этим именем отвергается. Неизвестные дополнительные JS, HTML, JSON, подкаталоги или ссылки → `INSTALLATION_PACKAGE_FILESET_CHANGED` и fail-closed. Существующие утверждённые JS всё равно нуждаются в полноценном code review — mirror hash сам по себе не доказывает безопасность кода.

Добавлены четыре synthetic fixture: лишний `.js`, лишняя директория, разрешённый но непрочитанный private config, symlink private config. В совокупности **45 офлайн-тестов**. Перед релизом отдельно сравнить файлы реально установленного распакованного расширения и build artifact; скан репозитория не знает, что было загружено в Chrome.

CI GitHub Actions теперь поддерживает `merge_group: checks_requested` помимо PR, а также ограничение параллельных устаревших workflow runs при частых pushes множества агентов. GitHub рекомендует отдельный merge_group trigger при required checks в merge queue; иначе merge queue не получает требуемые статусы. Отключение старой CI-ревизии оставляет её `cancelled/stale`, а **не** `passed`. Запуск `schedule` работает только если workflow уже доступен в default branch. Источник: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#merge_group

Проверка новой CI-матрицы: Windows/Ubuntu × Python 3.11/3.13, все четыре job должны подтвердить 45 тестов и исходный seven-file gate на фактическом проверяемом merge SHA; до публикации журнала последнего SHA не объявлять этот статус достигнутым.
