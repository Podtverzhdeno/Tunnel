# Hermes × GPT Web Companion — SDD объединения наблюдаемости и управления сбоями

**Ревизия:** 2026‑11‑22, `SDD-HERMES-GPT-INT-002`. **Статус:** PROPOSED; документация интеграции и отдельный тестируемый read-only фикс, не production deployment.  
**Репозитории:** `Podtverzhdeno/Tunnel` main `350ea33080bc35245aa6741f85fa926cc8a8fcaf` и `Podtverzhdeno/hermes-agent` main `7e9100901819ee44c16b4ddcb79a6bcb7909f591`.  
**Использование:** интеграционный контроль для десятков независимых агентов A0–A8+, владельца и ревьюеров; не разрешает новые Send, доступ к пользовательским сессиям или публикацию изменений без подтверждения.

## 0. Факты, ограничения и причинная граница

В **Tunnel**, а не только в `hermes-agent`, находятся исходники `Hermes-WebGPT/relay.py`, `chat-launch-smoke-desktop-test/web-companion/`, `web-extension/`, legacy monitor, экспериментальный AutoPilot и CDP. Два репозитория — отдельные boundary: коммит ядра Hermes не означает, что изменено локально установленное расширение.

- Legacy `web-extension/manifest.json` и `web-companion/bridge.py` в main подтверждают протокол **0.6.6** (второй проверяет heartbeat версии 0.6.6); исторический `docs/SEND_CONFIRMATION_FIX_20261111.md` описывает локально установленный Web Companion **0.9.7**. Исходники и SHA загруженного сейчас фонового service worker **НЕ получены**. Нельзя заменять живой 0.9.7 старым исходником 0.6.6.
- `Hermes-WebGPT/relay.py` source blob `9c30cbe864556fbf497ed2dea6619c58fd7156ff`. SQLite WAL, таблицы `requests,transport_routes,turn_bindings,page_owners`, один глобальный активный `claimed/intent/saved` запрос независимо от числа вкладок. `submit()` атомарно фиксирует `turn_id` и маршрут; `claim()` — `BEGIN IMMEDIATE`; `intent/saved/result` проверяются по lease и tab/page. Это существующий авторитетный источник для request/turn, не место для второго scheduler.
- Текущий `monitoring/queue_monitor.py` читает ровно `id,state,reason,created` в readonly SQLite `mode=ro` (учитывает WAL) и видит **age since creation**, не длительность стадии. Exit 0 означает только отсутствие новых тревог в рамках metadata; не работоспособность ChatGPT/Telegram.
- Legacy `web-companion/background.js` собирает `send_clicked` **после** `button.click()`; события отправляются best-effort. Это не durable предкликовый intent. `bridge.py` хранит tail 60 событий, что до патча позволяло ранним блокирующим признакам исчезать. Кандидат `fix/hermes-webgpt-sticky-recovery-evidence-20261122` улучшает удержание первой опасной записи и fail-closed на испорченном JSON. Он пока отдельная ветка и не доказывает исправления фактического 0.9.7.
- В `feat/hermes-autopilot-stable-sessions-20261114` на базе 0.8.5 есть supervisor, `session_control_v1.mjs`, `send UNKNOWN` fence, до пяти workers и тесты. Сама запись прямо говорит: **Send отключён**, пока не будет аутентифицированного Web Companion адаптера; реальное ChatGPT+Telegram E2E не подтверждено. Нельзя считать его готовым агентным контроллером.
- Связь `Local_WorkSpace` в ходе проверки сначала вернула `Session terminated`, затем список показал только scoped workspace `shelter`, не Windows-каталог Hermes. Поэтому current PID, порт Relay, реальный профиль/аккаунт, service worker SHA, Telegram ACK, локальная очередь и истинные причины пользовательского Stop — **UNKNOWN**. Не выдумывать метрики p95.
- В `hermes-agent` уже существует [SDD и независимый аудит API PR #20](https://github.com/Podtverzhdeno/hermes-agent/pull/20). Это **ядро/интеграционный уровень**, не замена данному расширению. Актуальный upstream NousResearch использует `/v1/runs` и native `browser_exec`/CDP; совместимость с форком и локальным Relay отдельно проверяется.

## 1. Целевая атомарность по всей системе

Проверяемая цепочка:

```text
Telegram inbound update (principal, chat, topic, update_id)
   -> Hermes Gateway auth/policy, tool-cycle ID
   -> Relay request/turn binding (transport=extension|playwright, lease, fingerprint)
   -> Web Companion/AutoPilot browser owner (profile, tab, document_epoch)
   -> durable send intent ACK [must precede click]
   -> click attempt [irreversible; ACK loss => UNKNOWN, never blind replay]
   -> authored user-turn saved proof (exact request ID)
   -> final assistant answer proof (same document/turn; not just Stop absent)
   -> validated tool request/result cycles OR final answer
   -> Hermes accepts bound final text
   -> Telegram outbound send + real provider receipt
   -> independent end-to-end verification/correlation
```

Every arrow is separately observed. **Relaying / Received / Tool call / Browser DOM reply / Done in SQLite / Delivered in Telegram are different states.** Each component reports only evidence it can justify. No agent narration upgrades evidence.

### 1.1 Decision contract per action

Required immutable identifiers: `origin_update_id, principal_ref, run_id, request_id, turn_id, attempt_id, step_id, effect_id, source_boot_id, transport, lease_epoch, session_revision, document_epoch, code_build, policy_digest`. Source IDs may be short-lived opaque aliases; diagnostic logs store HMAC/ref rather than raw prompt, UUID linkable to user, token or full URL.

Each status object includes `state`, `last_durable_transition`, `last_step_progress`, `last_observer_heartbeat`, `reason_code`, `hypotheses[]` (never represented as observed facts), `evidence_refs[]`, `unknown_effects[]`, `next_safe_action`, `owner`, `observed_at` and `source_sha`. Distinguish schema version and collection completeness/gaps. Producer-local `seq` is monotonic; wall clocks do not establish cross-host causality.

Browser command confirmation ladder (must not be collapsed): `RECEIVED -> VALIDATED -> DURABLE_INTENT -> CLICK_ATTEMPTED -> AUTHORED_TURN_SAVED -> RESPONSE_BOUND -> HERMES_RECEIVED -> TELEGRAM_DELIVERY_ACKED`. A missing confirmation after potential click is `UNKNOWN_POST_CLICK`, **not** `FAILED_NO_SEND`.

### 1.2 Authority ownership

| Effect / state | Единственный источник истины | Что запрещено считать доказательством |
|---|---|---|
| Telegram sender permissions and final outbound receipt | Gateway + Telegram provider receipt | report/LLM says «доставлено» |
| Request/turn/transport and admission | Relay SQLite, versioned CAS | tab existence, local variable in worker |
| Extension tab/page and document binding | owner-authorized browser broker with persisted fence | URL, raw tabId or arbitrary DOM |
| Tool offer/execution/return | Hermes tool policy and actual result | tool-call text generated ChatGPT |
| Report sha/ACK for Chain2 | existing Chain2/handoff owner | report filename or worker stopped |
| Multi-agent PR integration | GitHub HEAD CAS + CI + review owner | self-score 10/10, same-file overlap alone |
| Monitoring | read-only projection, no command authority | no alert ⇒ service healthy |

Avoid second independent operational database granting `SEND_ARMED`. In-flight browser effect spans SQLite and ChatGPT, so full ACID is impossible. Required property: durable intent first, at-most-one permitted click attempt per effect key, explicit uncertain state and verified external read-back before any new dispatch.

## 2. Multi-agent ownership and candidate overlap

**Observed 2026-11-22 snapshot:** 10 significant Tunnel branches compared against `main` `350ea330...`: 11 exact overlapping file paths across 2 branch pairs. This is not an exhaustive future PR inventory or proof of merge conflicts. The two overlaps are:

1. `hermes/edge-resource-supervisor-20261122` vs `hermes/edge-temporary-lifecycle-20261122`: **9 shared paths** (two web-extension and web-companion background modules, popup and tests). Resource-lifetime policies and Temporary chat semantics cannot be merged by concatenating event listeners.
2. `hermes/mcp-cdp-direct-20261114` vs `hermes/mcp-direct-web`: **2 shared paths**, including `hermes_web_cdp_mcp.py` and CDP send journal. Both branches predate 4 commits of current base (behind=4) and need rebase/semantic review.
3. Independent branches: `fix/hermes-relay-bounded-waiters-20261122` touches Relay waiter admission + tests; `fix/hermes-webgpt-sticky-recovery-evidence-20261122` touches Bridge recovery log + OpenSpec; `hermes/causal-queue-triage-sdd-20261122` contains an *independent read-only incident triager*. Do not create competing authoritative queue or duplicate triage notifications.
4. `feat/hermes-web-companion-readonly-doctor-20261122` adds read-only companion diagnostics, not proof of loaded 0.9.7. `feat/hermes-autopilot-stable-sessions-20261114` remains a draft non-sending controller.

**Integration ordering:** (A) exact loaded version/source provenance, (B) Bridge sticky blockers + reliability, (C) Relay waiter admission + status model, (D) one read-only event correlator with adapters, (E) single browser write-owner/fenced intent, (F) AutoPilot, temporary chats and high fanout as opt-in after independent E2E. Preserve old working rollback and unknown side-effect records. New agents work on separate branches/worktrees with restricted owned paths; files `web-companion/background.js`, `background_content.js`, `relay.py` and browser session tables are exclusive hot paths.

### 2.1 Review findings and concrete fixes

**F1 — ADMISSION_CAP_5 + GLOBAL_CLAIM_1 [OBSERVED].** `Queue.submit` under `BEGIN IMMEDIATE` allows at most five nonterminal jobs; `Queue.claim` allows only one global active job (`claimed/intent/saved`). Extra new turns receive `queue_full` (HTTP 400 in main and still 400 in PR #15), not a visible queued model request. A 30-agent supervisor must preserve rejected turns **outside** Relay under a fair, bounded authority and retry only no-effect rejections with stable turn IDs. Do not increase concurrency or queue caps until fencing, browser ownership, provider budget and recovery are tested.

### 2.3 Capacity math and stall-classification contract for 30+ agents

**Facts from exact Relay main:** `Queue.submit()` executes under `BEGIN IMMEDIATE` and rejects a **new** logical request when `COUNT(requests.state NOT IN ('done','blocked','uncertain')) >= 5`, raising `RelayError('queue_full')` *before inserting that request*. Separately, `Queue.claim()` permits only **one** global `claimed/intent/saved` turn. These are two distinct capacity limits: **five outstanding admitted requests; one executing turn**. Neither five browser tabs nor 30 ready agents override them. Existing `X-Hermes-Turn-Id` bindings are looked up before the cap, so replay of an already admitted turn should *reattach*, not allocate another admission.

**HTTP contract defect (observed):** fork `main` Handler maps every `RelayError` except `relay_shutting_down` to HTTP **400**, including `queue_full`. In PR #15, two new waiter-capacity errors map to **429**, but `queue_full` still maps to **400**. This is *admission saturation*, not malformed input; misclassification can terminate an orchestrator unnecessarily. Proposed 429 + bounded Retry-After (and explicit `QUEUE_FULL_NO_EFFECT_DISPATCHED` event) only for a verified **new** admission, preserving the exact same authenticated `turn_id`, payload and transport across retries. Authentication errors and ambiguous already-dispatched effects MUST NOT be retried under this rule. The handler response itself is not a durable scheduler.

**Illustrative capacity calculation — hypothetical service time, not real p95:** 30 agents calling the API simultaneously when the queue is empty will admit at most **5** *distinct pending turns*; the remaining up to **25** new turns receive `queue_full` unless an upstream fair dispatcher defers them until slots free. With a hypothetical **90-second** effective service time, the first 5 accepted turns ideally drain after **7.5 minutes**; the fifth starts after **6 minutes**. **Only if** a separate bounded/fair scheduler safely feeds the rejected 25 additional turns later, total work across 30 serial turns would take approximately **45 minutes**; the thirtieth begins after **43.5 minutes**. Those 25 unadmitted turns **do not appear** in Relay's `requests` and cannot be observed by its queue monitor. The theoretical `ceil(30/5) × 90 = 9 minutes` for five *simultaneously executing* lanes requires a different tested execution model; do not confuse it with a five-item admission buffer.

**Diagnostic policy:** `QUEUE_FULL_ADMISSION_REJECTED` (external scheduler observation, not Relay request), `QUEUE_WAIT_AGE` for admitted `queued`, and `ACTIVE_EFFECT_AGE` for `claimed/intent/saved` until trusted phase timestamps exist. No `MODEL_STALLED` from request creation age or a 400 response alone. A 600s created-age threshold can be exceeded by legitimate admitted queue waits for longer service times even with five slots. Real phase p50/p95 requires durable stage timestamps, worker heartbeat, lease and provider receipts. The observer must surface which layer supplied each reason and how many turns remain *outside* Relay.

**Admission controller for dozens of agents:** one outer queue with authenticated per-principal fairness, token budget, stable turn identity, bounded admission retry and read-back from Relay; never create 25 competing local retry loops. No more than one browser-writer per owned lane. A `queue_full` rejection must not corrupt a prior turn, switch transport, or erase an unknown effect. Add an explicit 30-worker fixture: exactly five distinct admissions maximum, one global active claim, exactly 25 typed no-effect rejections (for fully simultaneous new turns), and subsequent bounded handoff with unique stable turn IDs and zero duplicate Send. A mixed long reply/429/lost-ACK variant must preserve safety and declare `OBSERVABILITY_UNAVAILABLE` when truth cannot be obtained.

**F2 — MONITOR_MISSED_STATE_ROLLBACK [OBSERVED; patched in this branch only].** Old read-only queue monitor compared failure reasons but did not flag `done->queued`, `uncertain->done`, `saved->claimed` for same id+creation-time baseline. Created a conservative `illegal_state_regression` aggregate signal without exposing IDs or changing SQLite. Added five synthetic tests plus branch CI workflow. Does **not** infer the root cause or authorize repairs, and no local runtime claims. Allow snapshots to skip valid forward stages.

**F3 — SEND_INTENT_NOT_DURABLY_PRECOMMITTED [OBSERVED in legacy bridge].** `send_clicked` instrumentation follows `button.click()` and can be dropped; never use it as permission for duplicate Send. Need Relay-authenticated pre-click intent ACK tied to exact document/lease and one-shot browser effect ID. The actual installed v0.9.7 must be inspected independently.

**F4 — RECOVERY_BLOCKER_EVICTION [OBSERVED in main; separate candidate fix].** A cap of 60 diagnostic tail events may remove earlier evidence of `unexpected_close` or `report_disputed`. Review `fix/hermes-webgpt-sticky-recovery-evidence-20261122`: sticky earliest blocker + atomic replace and strict malformed-history 409, with tests. The patch does not make every telemetry event durable nor enable safe automatic retry.

**F5 — LEGACY_EXACT_PAYLOAD_DEDUP [SOURCE-LEVEL PRODUCT RISK].** Without `X-Hermes-Turn-Id`, `Queue.submit` fingerprints canonical request payload; repeating the *same exact request* intentionally can reuse an old terminal run indefinitely. For every new logical agent turn require a distinct authenticated turn key, while retry keeps the same key; do not infer new vs retry from payload text. This is a semantic/API compatibility problem, not proof of unwanted replay in production.

**F6 — VERSION_PROVENANCE_GAP [CONFIRMED].** Git main v0.6.6 does not prove running v0.9.7 bytes; source/install match does not prove currently loaded service worker. The branch with AutoPilot 0.8.5 explicitly does not send. Release gate records installed files SHA, browser extension ID/profile, running worker build, Relay runtime_build and relay/bridge PID/ports, separate confidence values.

**F7 — MULTIPLE BROWSER WRITE LANES [ARCHITECTURAL RISK].** Web Companion extension, Hermes native `browser_exec`, direct CDP/MCP and experimental AutoPilot could all target the same user tab. Require shared `browser_lane_id` + lease/fence and explicit owner consent. Until an authenticated transport adapter enforces arbitration, AutoPilot remains read-only/draft and distinct transport routes cannot fall back after `intent`.

**F8 — BROAD CPU/WAITER GROWTH [REVIEW TARGET].** Relay `ThreadingHTTPServer` currently creates ingress handler threads before application-level admission. `fix/hermes-relay-bounded-waiters-20261122` caps logical waiting subscribers, but check untrusted slow-header connection pressure separately, and validate status 429/Retry-After with no request admission. On 30+ agents, cap both resource dimensions and use rate-limited backoff/fair queue.

**F9 — TELEGRAM_RECEIPT_GAP [NOT LIVE VERIFIED].** `Relay.done` means a bound model result, never Telegram delivered. Gateway must log real `chat_id/topic/send_message message_id` result (with private data redacted in observer), not synthesize success from a model payload.

### 2.2 Результаты независимого контроля других PR (2026‑11‑22)

**P0 MIRROR DRIFT (#17 vs #16):** [PR #17](https://github.com/Podtverzhdeno/Tunnel/pull/17) добавляет `hasAnotherTabInWindow()` только в `web-extension/background.js`, но **не обновляет** `web-companion/background.js`, где `finish()` продолжает вызывать `chrome.tabs.remove(job.tabId)` без проверки последней вкладки. Тесты #17 намеренно читают `../web-extension/background.js`; поэтому могут быть зелёными при неодинаковых развертываемых копиях. [PR #16](https://github.com/Podtverzhdeno/Tunnel/pull/16) является кандидатом канонического seven-file `extension_mirror_guard.py` и должен **останавливать** такую интеграцию; его версия `0.6.6` — только reviewed-source contract, не подтверждение загрузки локальной версии 0.9.7. Оставлены конкретные замечания в #16 и #17. Источник: сравнение фактически изменённых файлов, а не воспроизведение в Edge.

**P1 ПОЛИТИКА ПОСЛЕДНЕЙ ВКЛАДКИ (#10/#11/#17):** #17 предлагает *сохранять последнюю вкладку*; #11 предлагает *перенаправлять последнюю владевшую вкладку в about:blank* после receipt, что меняет пользовательский вид и уничтожает текущий контекст вкладки; #10 вообще не включает обе гарантии в исходном `finish`. Recovery scan с разными периодами 120/300 секунд — конфликт политики, но эти цифры сами по себе не измерение оптимального p95. До слияния выбрать **одну** поведенческую стратегию и прогнать пользовательские сценарии: последний tab/window, активный ручной черновик, две вкладки разных агентов, запрет закрытия после спорного ACK, восстановление MV3. Изменение `about:blank` — внешнее действие, не “безопасная операция” без согласованного намерения владельца.

**P0 СОВМЕСТИМОСТЬ OBSERVER→TRIAGER (#19 vs #13):** [PR #13](https://github.com/Podtverzhdeno/Tunnel/pull/13) использует строгий список `ALERT_CODES` и пока не распознаёт введённый здесь `illegal_state_regression`. При одновременном принятии двух веток `incident_sampler` классифицирует такой отчёт как `observation_unavailable`, сохраняя baseline и не повторяя Send; это **безопасно по эффектам, но теряет конкретную причину отката**. Автору #13 направлен review с контрактом `QUEUE_STATE_REGRESSION`, allowlisted `from->to`, без raw ID и с test roundtrip из реального queue_monitor. Нельзя отключать strict allowlist ради нового кода.

**P0 ГОТОВНОСТЬ RELAY ИСТОЧНИКА:** отдельная проверка `Queue.event()`, `cancel()`, `expire()` на main подтвердила, что терминальные `done/blocked/uncertain` действительно не переходят обратно через штатные методы; `saved` не может вернуться в `claimed`. Поэтому read-only `illegal_state_regression` соответствует проверенному текущему Relay. В будущей миграции с ручным разрешением неопределённости контракт нужно пересмотреть: законная корректировка должна оформляться новым ID/проверяемой correction-событием, а не неявным переписыванием существующей терминальной записи.

## 3. Stop-reason decision tree

1. **Can authority be read?** If no: `OBSERVATION_UNAVAILABLE` and maintain previous unknown/safety fences. Do not mark crashed.
2. **Was external effect already possible?** Durable intent/click/Save or legacy uncertain? If yes and outcome unknown: `NEEDS_RECONCILIATION`; block transport switch, retry, new session reuse.
3. **Approval, quota or user priority?** `APPROVAL_REQUIRED`, `ACCOUNT_LIMIT`, `AUTH_CHANGED`, `MANUAL_DRAFT` or `OWNER_STOP`: explicit pause with only owner-authorized resolution.
4. **Agent heartbeat but no progress?** `STALLED_NO_PROGRESS`, possibly model still generating or tool being called. Check `last_heartbeat` vs `last_verified_transition` separately, not one timer.
5. **Lost transport while browser action underway?** `BRIDGE_DISCONNECTED`, `TAB_DOCUMENT_CHANGED`, `MV3_RESTART` or `CDP_DETACHED`. Reattach read-only, prove same lease, never duplicate click.
6. **Content observation failed?** `SEND_UI_NOT_READY`, `RESPONSE_NOT_BOUND`, `SCHEMA_INVALID`: distinguish from model error, preserve actual text only in private already-authorized state.
7. **Relay done but no Telegram ACK?** `TELEGRAM_DELIVERY_UNKNOWN` and separate outbound reconciliation; never replay model query to fix delivery.
8. **Two trusted sources disagree?** `EVIDENCE_CONFLICT`, not majority voting across ten agents. Assign independent verifier and owner.

Every incident should record symptom, two competing hypotheses, evidence with exact SHA, falsifying test, safe next action, age/deadline, owner and rollback. Read-only monitors may suggest patches but cannot execute external effects or mutate task states. The first `unknown` is useful engineering evidence, not a nuisance to auto-dismiss.

## 4. Fault-injection contract

| ID | Injection | Acceptance |
|---|---|---|
| T01 | Two agents claim same `turn_id` with identical payload | one request/transport lease; later attach only, no second Send |
| T02 | Same turn key with changed body or transport | typed conflict before any external effect |
| T03 | Transport ACK lost immediately after durable intent | sender noReplay + `UNKNOWN` until read-back |
| T04 | Content script clicks, then crashes before `send_clicked` telemetry | no silent retry; pre-click durable intent still exists |
| T05 | First recovery warning pushed out by 70 later events | sticky blocker still blocks unsafe recovery |
| T06 | Diagnostic JSON malformed and new event arrives | fail closed 409 without replacing previous history |
| T07 | 50 concurrent HTTP subscribers | bounded waiters, no orphan claim, explicit capacity, no side-effect replay |
| T08 | Terminal `done` unexpectedly becomes `queued` in same DB | read-only monitor `illegal_state_regression`, no private IDs |
| T09 | `saved` unexpectedly reverts to `claimed` | read-only regression alert, no automatic correction |
| T10 | Baseline skips valid `queued -> saved` stages | no false regression |
| T11 | Manual draft is nonempty or user changes focus | no clear/send, preserve user's text |
| T12 | Worker MV3 suspended after claimed; tab navigated | `document_epoch` binding rejected, no new write |
| T13 | Another browser adapter attempts same CDP tab | second writer rejected; read-only observation permitted |
| T14 | External account sign-in expired or quota reached | owner pause; no navigation bypass |
| T15 | Assistant replies with wrong `request_id` or made-up tool args | parser rejects; Hermes tool cannot execute |
| T16 | Model result persisted but Telegram network ACK lost | Relay state remains done, delivery unknown; no model rerun |
| T17 | Browser tab closed during unknown Send | preserve pending effect and noReplay fence; do not kill owner browser |
| T18 | Old branch merged with new upstream without wheel/test matrix | release fails, no automatic promotion of a self-report |
| T19 | One observer crashes or disks fail | task authority unchanged; observation gap and operator alert |
| T20 | Long tool cycle and context near 64K | explicit budget, preserve tool pairs, no fabricated results |
| T21 | 30 simultaneous new turn IDs against empty Relay | at most 5 admissions, 25 typed capacity denials; one active claim, zero new effects from denied turns |
| T22 | Same turn ID retry during full admission buffer | existing bound turn reattaches, no second request; altered body/transport conflicts |
| T23 | Relay `queue_full` under owner-approved retry policy | typed capacity status, stable Retry-After and turn identity; never retry unknown post-click actions |

**Proof classes:** T01–T10 and T21–T23 can use offline unit/SQLite/browser fakes; T11–T17 need owner-authorized isolated Chrome/Edge tests and finally an explicit real signed-in E2E; T16 additionally needs real Telegram delivery. Unit tests cannot prove these endpoints actually work in the user's current browser. Keep all synthetic evidence labelled `fixture`.

## 5. Weighted idea triage (explicit estimates; no invented measurements)

Weights: reliability 30%, security 25%, causality 20%, lower implementation burden 15%, operator usability 10%. Each category 0–5. Score is a **prioritization estimate**, not test pass rate or percentage completion.

| Candidate | R / S / C / E / U | Weighted /5 | Disposition |
|---|---|---:|---|
| Единый Relay send-intent + проверяемый read-back | 5 / 5 / 5 / 2 / 4 | **4.45** | P0 |
| Сохранение sticky-блокеров восстановления | 5 / 5 / 4 / 4 / 4 | **4.55** | P0 |
| Сквозная причинная временная шкала и gap-детектор | 5 / 4 / 5 / 3 / 5 | **4.45** | P0/P1 |
| Безопасная изоляция вкладок и transport fencing | 5 / 5 / 4 / 2 / 4 | **4.25** | P0 |
| Отдельный ACK реальной Telegram-доставки | 5 / 4 / 5 / 3 / 4 | **4.35** | P0 |
| Read-only монитор регрессий состояний | 4 / 4 / 4 / 5 / 4 | **4.15** | P1 |
| Bounded waiters и fairness 30+ агентов | 4 / 4 / 4 / 4 / 4 | **4.00** | P1 |
| Внешний fair admission: 5 pending / 1 claim / stable turn_id | 5 / 4 / 5 / 3 / 5 | **4.45** | P0 |
| Автопереключение Web Companion → CDP при таймауте | 1 / 0 / 1 / 5 / 2 | **1.45** | REJECT |
| Ещё один независимый авторитетный scheduler | 1 / 1 / 2 / 2 / 1 | **1.35** | REJECT |
| Сначала красивый дашборд, потом квитанции | 2 / 3 / 2 / 3 / 5 | **2.70** | DEFER |

Priority must be revised after actual CPU, provider p95, false-positive/duplicate-send and human-recovery metrics. A concept with high qualitative score is not automatically mergeable.

## 6. Canonical multi-agent roles, handoff and gate

| Role | Owned deliverable | Forbidden authority |
|---|---|---|
| A0 coordinator/architect | SDD index, boundaries, integration timeline, immutable reviews | self-approval of own production P0 |
| A1 Hermes Gateway | Telegram auth/permissions/tool cycle and final delivery | editing extension or UI session |
| A2 browser adapter | per-tab/document binding, pre-click fence, MV3 recovery | provider receipt fabrication |
| A3 Relay | SQLite leases, turn identity, queue admission, CAS, outbox | silently changing original user turn |
| A4 observability | read-only correlator, typed alerts, incident log | duplicate scheduler, automatic retry |
| A5 chaos QA | reproducible fault fixtures and independent merged SHA tests | deploying browser on user machine |
| A6 security | secrets/permissions, threat model, authz boundaries | bypassing owner approval |
| A7 release | exact hashes, source/install/loaded verification, rollback | ignoring uncertain effects |
| A8 independent reviewer | reject/accept evidence and gate rationale | merging own unaudited change |

Before editing: `agent_id, task_id, parent, branch, base_sha, owned_paths, lease, resource_claim, risk, test, rollback`. After: `head_sha, changed_paths, tests_run_at_SHA, falsifying_test, observed_vs_hypothesized, output_artifact_ref, unresolved_effects, next_owner`. A Git branch is NOT a distributed runtime lease; use actual coordinator CAS. If a child disappears, retain its last durable evidence and assume any external effect may have happened. Avoid an endless cascade of replacement agents that repeats uncertain actions.

**Gate G0:** installed v0.9.7 provenance; snapshot and owner consent, no active job.
**Gate G1:** one authoritative send-intent and read-back protocol, preserving legacy routes.
**Gate G2:** source-level fixes and offline tests for sticky warning, bounded waiters, queue monitor.
**Gate G3:** compare and resolve Edge resource/temporary branch overlap at merged SHA; test 1/2/5 worker ownership.
**Gate G4:** signed/authenticated Web Companion adapter and independent browser receipt, never DOM claim alone.
**Gate G5:** actual owner-approved Windows ChatGPT + Hermes tool + Telegram roundtrip, real provider receipt.
**Gate G6:** cautious opt-in 1→5 sessions, memory/CPU/p95, stop cause metrics, rollback/noReplay preservation.

Target metrics (to measure; **none measured live in this audit**): 0 duplicate sends, 0 false terminal success, 100% unknown effects with explicit owner+reconciliation action, no stage state regression without alert, 100% exact-SHA releases, bounded resource usage under 30+ agents. Define denominators and sampling windows before publish.

## 7. Audit log — append-only review notes

- **2026-11-22 10:38+ UTC approximate / source audit:** `hermes-agent` fork branches and official upstream; draft [integration SDD PR #20](https://github.com/Podtverzhdeno/hermes-agent/pull/20) opened. No local browser access.
- **2026-11-22 / project identity recovered:** Web Companion/Relay sources located in `Podtverzhdeno/Tunnel`; do not mistake old fork-level API SDD for actual extension implementation.
- **2026-11-22 / Tunnel overlap scan:** 10 branch heads vs fork main; 11 shared paths across 2 branch pairs. Two Edge branches conflict on same nine paths; two CDP branch variants share two files. These are path review triggers, not confirmed textual merge conflicts.
- **2026-11-22 / findings:** Global one-request Relay claim; current monitor has no impossible transition alerts; legacy UI click event best-effort; branch sticky warnings protects old recovery evidence; AutoPilot send disabled; running version unverified.
- **2026-11-22 / isolated code change:** `monitoring/queue_monitor.py` now emits aggregate `illegal_state_regression` for terminal/phase rollback. Five targeted tests and `.github/workflows/hermes-queue-observer-integrity.yml` committed on a new branch. No SQLite mutation, no Browser Send, no live Windows work. Verification is recorded separately below; historical test status must not be confused with final-head status.
- **2026-11-22 / CI RED — confirmed environment dependency:** the initial workflow run [45015847452](https://github.com/Podtverzhdeno/Tunnel/actions/runs/45015847452) executed 44 test entries: 43 passed, including all five new tests; the unrelated `test_confirmation_observation.py` failed **before its test cases ran**, importing missing `queue_monitor_baseline` (GitHub main does not contain the module at the checked paths). This is a real **full discovery blocker**, not a regression test failure, and remains unsolved in that broad suite.
- **2026-11-22 / CI targeted fix:** CI scope corrected to `test_queue_monitor` + `test_quality_observation` with explicit note that the external baseline-driver test is excluded. Full suite is **not claimed green**; the missing module needs its own owner/source restore and a separate full-suite gate before shipping the whole monitoring package. Targeted workflow run must be verified on current SHA.
- **2026-11-22 / CI focused GREEN:** [workflow 45016048518](https://github.com/Podtverzhdeno/Tunnel/actions/runs/45016048518) passed on exact first CI-fix commit `58ed5ae21b76a1a655cd8b74ec241539d1acdf85` (focused queue+quality regression only). Later documentation commits do not change the tested Python logic or workflow, but **current final branch SHA** must still be checked for release. Broad discovery still has missing `queue_monitor_baseline` import; no full-suite pass claim.\n- **2026-11-22 / additional peer review:** PR #17 is not mirrored into `web-companion/background.js`; PR #16 mirror gate should block this, PR #13 triager requires new alert schema mapping; left direct review comments. Verified Relay terminal-state invariants in source. No local/production reproduction.\n- **2026-11-22 / CAPACITY_CORRECTION:** Source reviewed `Queue.submit` and Handler status mapping. Corrected previous simplistic “30 tasks all queued” assumption: Relay accepts at most five outstanding distinct turns, executes one globally and returns HTTP 400 `queue_full` for additional turns. PR #15 handles new waiter-cap 429s but not this existing `queue_full`. Added deterministic admission calculation and T21–T23; no real latency or active queue sampling claimed.\n- **2026-11-22 / critical boundary:** Local Workspace only exposed `shelter`; no user Hermes repo root. All current daemon health, GUI/session state and remote effects are `UNVERIFIED`.

## 8. Existing related SDD and technical references

- [Hermes monitoring/queue baseline](../../docs/HERMES_MONITORING_RU.md) and [functional requirements](../../docs/HERMES_FUNCTIONAL_SPEC_RU.md) from Tunnel main.
- Peer [atomic-observability SDD](https://github.com/Podtverzhdeno/Tunnel/blob/fix/hermes-webgpt-sticky-recovery-evidence-20261122/Hermes-WebGPT/docs/ATOMIC_OBSERVABILITY_SDD_20261122.md) contains sticky-warning fix and recorder privacy controls; keep reviewed overlap, do not replace it silently.
- Peer [causal incident triage proposal](https://github.com/Podtverzhdeno/Tunnel/tree/hermes/causal-queue-triage-sdd-20261122/Hermes-WebGPT/openspec/changes/add-causal-observation-triage) is the candidate read-only correlator, not a second Relay.
- Peer [temporary-chat v2 protocol](https://github.com/Podtverzhdeno/Tunnel/blob/hermes/edge-temporary-lifecycle-20261122/Hermes-WebGPT/docs/HERMES_TEMPORARY_CHAT_V2_PROTOCOL.md) is NO-GO; do not route old `/c/` semantics into a Temporary session.
- W3C Trace Context: https://www.w3.org/TR/trace-context-1/ ; OTel GenAI semconv https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Chrome extension service worker lifecycle https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/ ; Native Messaging https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- SQLite WAL locking and recovery https://sqlite.org/wal.html
- GitHub branch protection / merge queue https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue

**Обязательное ограничение:** SDD описывает целевой контракт и источник проверенных фактов, а не живую автономную сессию. Истинное состояние Hermes невозможно вывести из GitHub SHA или отсутствия ошибок в офлайновом тесте.
