# Hermes: Temporary Chat + Edge Runtime RFC (2026-11-22)

Status: design proposal; **NOT DEPLOYED**, not proven by Windows E2E. Owner requirement: every new agent session must start at `https://chatgpt.com/?temporary-chat=true`. The legacy default branch remains unchanged.

## 0. Evidence and code-level diagnosis

Inspected current main files:
- `chat-launch-smoke-desktop-test/web-extension/manifest.json`: MV3 v0.6.6, description explicitly specifies closing completed tabs.
- `web-extension/background.js`: `dispatch()` creates `https://chatgpt.com/#chat-launch-smoke=bg-<job-id>` inactive tab; `finish()` / `retryAcceptedClosure()` call `chrome.tabs.remove`; `CLAIMS_PER_POLL=4`, recurring alarm 0.5 minutes, `CAP=25`; ownership/reconciliation relies on persisted `/c/<id>` URLs.
- `web-extension/background_content.js`: requires route `/`; sending/confirmation relies on `/c/<id>`; fast path reports "submitted" after minimum hold, potentially while model is still generating.
- `web-companion/start_bridge.py`: intentionally starts only bridge process, no browser window. Therefore **extension-driven tab closure is confirmed, whole-browser exit remains a hypothesis**, e.g. last-window closure or a separate launcher/supervisor. Do not claim Windows PID-level root cause without reproducing.
- `background.js`: tab closure is gated in report mode by immutable receipt and recovered ownership; do not weaken existing independent ACK or fail-closed semantics just to support temporary chats.

## 1. Non-negotiable invariants

1. Launch URL MUST be exactly `https://chatgpt.com/?temporary-chat=true` before any job is typed. Hash-based job assignment must not mutate this URL at launch.
2. Never assume the query parameter is definitive proof of temporary mode. Verify an explicit temporary-chat mode signal from *supported UI*; if it is missing/ambiguous, **NO SEND**. Record `temporary_mode_unverified` without capturing account identifiers.
3. Never redirect a conversation into permanent history as a workaround. Do not use /c/ persisted routes as a requirement or as a fallback.
4. Never auto-close browser windows, never close tabs with unconfirmed identity, and never close an agent tab before a durable result report / explicit user dismissal. Default keep-tab policy initially, with later optional close-after-ACK only when confidently proven.
5. Browser process belongs to an independent supervisor, not the ephemeral MV3 service worker. A service worker can be suspended; its lifetime is not the browser lifetime.
6. At-most-once SEND per job ID, including after refresh, crash, closed tab, bridge timeout, extension reload or UI ambiguity. Uncertain send state requires operator reconciliation, never re-send.
7. No credentials/cookies/tokens/prompt content in debugging, URLs, extension telemetry or repo commits. CDP must bind only to localhost and isolated user data dir, never public network.
8. Preserve original report-integrity/ACK validation and local bridge loopback authorization. Do not mark "done" solely because a ChatGPT assistant message exists.
9. Manual user browser close is respected. Never silently respawn browser or reopen a private session after deliberate close.

## 2. Proposed architecture

```text
Windows tray supervisor (owner of dedicated Edge subprocess/profile)
    ├── browser health / PID / top-level window / opt-in restart
    ├── resource governor (CPU%, RSS, tab count, interaction latency)
    └── Local bridge (durable SQLite jobs, report ACK, event journal)
            ↕ authenticated localhost channel
       MV3 extension worker (event orchestration, recoverable)
            ↕ runtime messaging (job_id, lease token, no URL hash)
       Content script (temporary-mode verification, UI composer, generation state)
            ↕
       ChatGPT temporary tab(s), bounded live concurrency
```

Prefer extension APIs for navigation/DOM lifecycle; CDP only if a separately justified experiment requires it. Do not run two competing controllers against the same tab. If CDP is used, isolated profile + loopback, securely scope access; avoid remotely exposed debugging ports.

### 2.1 Session table (durable bridge)

`job_id` UUID PK; `generation` integer; `state` enum; `tab_guid` opaque random ID; `tab_id` nullable ephemeral; `window_id` nullable; `profile_id` hash alias, never local profile path in telemetry; `created_at`; `last_heartbeat`; `send_attempted_at`; `send_observed_at`; `temporary_verified_at`; `result_ack_id`; `close_eligible`; `terminal_reason`.

Persist before invoking risky UI action and commit as atomic transition. Browser tab IDs are never identity, and may change after restart.

### 2.2 States

QUEUED -> RESERVED -> TAB_OPENING -> TEMP_MODE_VERIFYING -> READY -> SEND_INTENT_RECORDED -> SENT_UNCONFIRMED -> SENT_CONFIRMED -> RUNNING -> REPORT_WAIT -> ACKED -> IDLE_PRESERVED.

Alternative transitions: NO_SEND_BLOCKED, AUTH_REQUIRED, UI_CHANGED, TAB_LOST, REPORT_DISPUTED, SEND_AMBIGUOUS, USER_CLOSED, EXPIRED_UNRECOVERABLE. Never automatically transition from SENT_UNCONFIRMED, SEND_AMBIGUOUS or TAB_LOST to READY.

Before clicking send: fsync/durable record `SEND_INTENT_RECORDED`. On ambiguity after click, use no retry. Distinguish "user message visible" from independent "report delivered and verified".

### 2.3 Temporary-mode UI confirmation

Open exact URL with `chrome.tabs.create({url: TEMP_URL, active: false, windowId})` or navigate existing verified owned idle tab. Wait for document ready and stable composer; inspect accessible semantic UI for temporary-chat banner/label/status. **Do not hardcode an undocumented selector as an immutable guarantee**. Feature adapter contains selector candidates and tests against snapshots; after any redesign fail closed.

The initial URL can change when UI hydrates or navigates. Do not require permanent /c/ URL. Associate tab and job through extension memory + durable bridge signed lease/job association, sender.tab.id and content script injected in the owned tab. Never accept messages from another tab. Confirm temporary mode immediately before the single send. If page leaves temporary mode, stop automation, preserve tab, require manual reconciliation.

Assignment channel: replace URL hash job ID with `chrome.tabs.sendMessage(tabId, {type:'hermes-assign', jobId, generation})` after content script ready. Content script acknowledges only when URL origin valid, tab ownership established, and temporary mode verified. The bridge knows canonical job identity independent of URL. Never place prompt or token in URL or telemetry.

### 2.4 Browser lifetime

A dedicated Edge profile directory is separate from personal browsing; keep the profile persistent across supervised runs to preserve login **while understanding temporary chats themselves are not persistent**. Acquire a single-instance supervisor lock. If browser already live, attach to the existing owned browser, not launch duplicate. Browser PID death detection is distinct from tab removal and MV3 worker sleep.

Policy for completion: **keep session tab initially**. Later optional explicit "close only after verified ACK" without allowing closure of the final browser tab; do not use a fake background keeper tab as proof of browser health. For `USER_CLOSED`, record loss and pause, never auto reopen. On unplanned crash, restart only if configured and only into neutral blank/launcher page; do not automatically resubmit a task. Preserve durable state for manual recovery.

### 2.5 Scheduling and resource governor

Start **max 1 concurrently hydrating ChatGPT tab**; initial `max_running=2`, `max_tabs=3` are experimental *defaults*, not claimed optimal values. A/B test 1 vs 2 vs 3 live tabs and tune to observed 95th percentile CPU, RAM, page interactivity, report duration and error rate. Replace 30-sec O(all tabs) aggressive recovery scan with event-driven `onCreated`/`onUpdated`/`onRemoved` and bounded periodic reconciliation (60–120 s, jitter); every poll must have upper work budget. Avoid DOM-wide `document.body.innerText` scans on each cycle; prefer targeted `MutationObserver` with throttling and disconnect when idle. No overlapping full-page observers.

No software can prevent active ChatGPT UI from using CPU; reductions come chiefly from controlling simultaneously active/rendering tabs, bounded hydration, and fewer scans. CPU measurement must include Edge browser, renderer, GPU and utility subprocesses (aggregate by process tree), not just supervisor PID. Capture 1/5/15-minute CPU and working-set measures, GPU process counts, peak RAM, latency, send failure and ACK success.

### 2.6 Edge optimizations: safe vs hazardous

Safe candidates in **dedicated Hermes profile only**, after benchmarking:
- Turn off Startup Boost and "continue running background extensions/apps when Edge is closed" where unnecessary; these lower idle process count but do not guarantee live browser persistence.
- Disable optional new-tab content, shopping/sidebar/recommendations and nonessential extensions via user settings, not invasive registry deletion.
- Disable preloading/prefetching if measured beneficial.
- Block background tabs from producing parallel browser agents by scheduler concurrency, **not** by sleeping an active agent.
- Permit sleeping tabs for completed **idle** work; exclude `chatgpt.com` while a task is active (the Edge SleepingTabsBlockedForUrls policy may be too broad if all chats share origin; use profile-specific behavior and measure).

Dangerous / NOT default:
- `--disable-gpu`, `--no-sandbox`, `--disable-web-security`, `--single-process`, `--disable-site-isolation-trials`, `--disable-features=...` grab-bags: could break rendering, security, login or platform features; not recommended.
- Disabling JavaScript, cookies, service workers, storage or WebSocket/streaming: breaks ChatGPT.
- Forcing "sleeping tabs" on active, streaming agent: pauses or delays completion and heartbeats.
- Global machine registry policies for the user's normal browser: prohibited by scope. Apply per-profile UI settings when possible; machine policy requires explicit opt-in and rollback.

Edge policy references: https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsenabled ; https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsblockedforurls ; https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/backgroundmodeenabled ; https://learn.microsoft.com/deployedge/microsoft-edge-policies/startupboostenabled

### 2.7 Privacy and session implications

Temporary chats are not regular /c/ history entries and should not be treated as durable browser-side storage. Reports must be persisted by the authorized local bridge BEFORE allowing session teardown. An unacknowledged temporary chat could be irrecoverable after browser crash. Log only job IDs, redacted state transitions, timestamps, hashed diagnostics. Temporary mode is about ChatGPT conversation retention, **not** an authorization bypass or guarantee of local confidentiality.

## 3. Why browser may be closing

Confirmed: legacy extension explicitly removes the ChatGPT tab; if it was the last tab, the window may disappear. Not confirmed: any process-level `taskkill`, `subprocess.Popen` parent ownership, automation context manager `browser.close()`, shutdown hook, crash, sleeping-tab discard, resource pressure, browser update. Reproduction logging must distinguish:
- `chrome.tabs.onRemoved` with `removeInfo.isWindowClosing`
- `chrome.windows.onRemoved`
- supervisor Edge PID exit code and Windows Event Log
- `chrome.tabs.onUpdated({status, discarded})`
- extension worker startup/suspend/reload
- bridge availability / heartbeat / task state

Never attribute a full browser exit solely to MV3 service-worker suspension.

## 4. Implementation phases (do not skip gates)

P0: capture baseline + duplicate-free browser launch, process tree logging and tab/window close origin. Golden fixtures for legacy regular-chat flow.

P1: add `TEMP_URL` and distinct temporary-mode verification adapter + isolated tests; change launch to exact URL; replace fragment handoff with tab messaging; DO NOT send unless verified. Release flag default OFF until E2E validated.

P2: decouple report ownership from `/c/` URL; durable job-to-tab lease; preserve legacy independent report ACK and no-retry; replay/failure tests (MV3 reboot, crash, disconnect).

P3: implement Edge supervisor dedicated profile with opt-in launch and non-destructive lifecycle; never kill normal Edge; local status/health diagnostics.

P4: resource governor, event-driven recovery, backpressure, concurrency cap and diagnostics. A/B test before aggressive Edge settings.

P5: Windows end-to-end acceptance, rollback packaging, migration guide and version bump only when successful.

## 5. Acceptance matrix (all must be green)

| Test | Required observation |
| --- | --- |
| T01 fresh task launch | Initial navigation exactly `https://chatgpt.com/?temporary-chat=true` |
| T02 temporary mode missing | No send; diagnostic reason; tab preserved |
| T03 temporary mode leaves on hydration | No send, no fallback permanent chat |
| T04 active generation > 10 min | Browser/tab remains alive; no synthetic done |
| T05 job completed + external report ACK | ACK stored independently; result persisted; tab preserved |
| T06 no ACK | No closure and no done |
| T07 manual browser close | No automatic relaunch or resend |
| T08 unexpected renderer crash | State ambiguous, no resend; operator notice |
| T09 service worker terminated/reloaded | Durable state restored without duplicate send |
| T10 bridge offline during receipt | Tab preserved, no invented ACK, bounded retry of receipt only |
| T11 two tabs same-looking content | Ownership ambiguity -> no action/closure |
| T12 user normal Edge running | Dedicated supervisor never closes or alters normal profile |
| T13 private chat contents | No prompt in URL, diagnostics, error reports or Git commits |
| T14 30/60/120-min soak | No tab/process leak, no accumulated timer/observer |
| T15 throttling experiment | Lower aggregate CPU without increased dropped/ambiguous tasks |
| T16 Edge sleeping | No active task suspended/discarded |
| T17 post-update ChatGPT UI changed | Fail closed, no send to regular conversation |
| T18 retry of ambiguous SEND | At-most-once persisted guarantee |
| T19 Windows restart | No unapproved auto-send on recovered tab |
| T20 rollback | Legacy workflow restored with unchanged production profile/config |

## 6. Benchmark methodology

Use controlled identical job mix on the same Windows host, fixed Edge build, power plan, screen state and network. Record idle baseline (5min), 1 active ChatGPT session (10min), 2 sessions (10min), 3 sessions (10min), 60min mixed soak. Repeat each at least 3 times, randomize order, report median and p95, confidence intervals if enough samples. Acceptance target proposed (not measured): **>=25% reduction** in median aggregate browser CPU against current extension at equivalent completed task throughput, no increased ambiguous sends or missing ACKs. Reject any optimization breaking login, streaming or browser stability.

## 7. Critical review / unresolved blockers

- The required URL is an entry request, not guaranteed stable after navigation. Validate temporary state in the UI before send.
- UI DOM selectors are fragile; periodically verify against current ChatGPT.
- Edge may suspend workers/background pages; avoid relying on content-script timers for correctness.
- A temporary ChatGPT conversation cannot be used as canonical durable report repository. Store reports locally and use receipt proof.
- Confirm exact user-reported "browser closes" with event/pid trace; only tab-close cause is already proven.
- Check OpenAI service terms applicable to the intended automated workflow; prefer supported APIs for high-volume background tasks where suitable rather than attempting to evade rate limits or challenges.
- Do not flip flags or release changes until Windows E2E can be run.

## 8. Source links

- https://help.openai.com/en/articles/8914046-temporary-chat-in-chatgpt
- https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/
- https://developer.chrome.com/docs/extensions/reference/api/tabs
- https://developer.chrome.com/blog/remote-debugging-port
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsenabled
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsblockedforurls
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/backgroundmodeenabled
- https://learn.microsoft.com/deployedge/microsoft-edge-policies/startupboostenabled
