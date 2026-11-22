# Hermes / Edge Autopilot — browser lifecycle, CPU budget and conservative migration

**Date:** 2026-11-22. **Status:** candidate only, isolated branch. No claim of live Windows deployment, browser-process diagnosis, measured CPU improvement, successful ChatGPT temporary-chat operation, or Telegram E2E. Do **not** merge or install without tests and version reconciliation.

## 0. The actual boundary

This repository contains two related but noninterchangeable components:

* `chat-launch-smoke-desktop-test/web-companion` + mirrored `web-extension`: Chromium MV3 browser automation, local loopback bridge, bounded fanout, immutable completion artifact acknowledgments and `chain3_continuous.py`.
* `Hermes-WebGPT`: Hermes Gateway/Relay and an independent optional `web-chatgpt-high-cdp`/Playwright transport. Its Windows `hermes_windows_browser.py` **discovers Chrome before Edge** unless `AGENT_BROWSER_EXECUTABLE_PATH` was explicitly configured.

The published MV3 candidate uses manifest version **0.6.6**, including the 0.6.6 compatibility heartbeat in `bridge.py`. Other installations/portable archives may contain 0.8.5/0.9.7 variants. These are not interchangeable. Source on GitHub, extension loaded by a browser, relay source, process actually running and user-selected transport must be separately identified.

The code inspected here binds completed ChatGPT tasks to ordinary persisted `https://chatgpt.com/c/...` routes. It is **not proof of actual Temporary Chat support**. If a temporary session does not produce this route and an independently verifiable completion artifact, ownership and completion checks must **fail closed**. Do not change the URL/DOM proof to accept arbitrary anonymous temporary tabs merely to make a demo pass. Temporary chats are not a durable task database.

## 1. Observations versus hypotheses

| Observation from reviewed source | Interpretation | Evidence required to establish root cause |
|---|---|---|
| `background.js` calls `chrome.tabs.remove(tabId)` at terminal result and verified ACK. | The last tab can close the last normal browser window; this is a plausible explanation for the disappearing UI. | Exact owned `tabId`, `windowId`, sibling tab count, `chrome.windows.onRemoved`, Edge parent/child PIDs and process exit time. |
| `CAP=25`, `CLAIMS_PER_POLL=4` and a 30-second alarm. | 25 independently hydrated ChatGPT SPA pages can consume substantial CPU/RAM, dependent on machine and content. | Separate browser baseline, 2/6/25-tab measured CPU/RAM, extension worker duration and browser task manager breakdown. |
| `recoverOrphanedTabs()` runs every poll and `verifiedOwnedTab()` scans tabs with DOM probes. | Recovery and identity verification create avoidable additional work, especially at 25 tabs. | Count `executeScript` calls and duration per poll; separate one-time recovery scans from ACK verification. |
| `start_bridge.py` never starts a browser; `hermes_windows_browser.py` does not own all browser lifecycle paths. | CDP/playwright launch/close and external process supervisor might also cause apparent process exit. | Logged process creation/death, actual CDP connection ownership, active startup flags; never infer from extension source alone. |
| The UI automation expects saved `/c/` URLs. | A genuine Temporary Chat workflow may not satisfy this protocol at all. | A user-authorized real browser test with actual temporary mode, no private API, and a scoped completion proof. |

The *window vanished* incident must be classified as one of: (A) final tab closed, (B) browser process exited/crashed, (C) a CDP owner terminated the browser, (D) user/OS closed the window, (E) login/profile handoff moved the visible tab to another process/window. These are different defects and require different fixes.

### Additional confirmed source: native Hermes browser cleanup

The **separate, older** `Podtverzhdeno/hermes-agent` fork contains a destructive browser cleanup path in `tools/browser_tool.py`, independent from Web Companion:

- `BROWSER_INACTIVITY_TIMEOUT` defaults to **300 seconds**; its background thread checks periodically (~30s).
- `browser_close()` and process-exit `_emergency_cleanup_all_sessions()` both lead to `cleanup_browser(task_id)`.
- Until the separate candidate patch, cleanup unconditionally issued agent-browser `close` and attempted `os.kill(daemon_pid, signal.SIGTERM)`.
- A user-supplied external CDP Edge session is flagged `features.cdp_override=true`, but cleanup previously did not distinguish browser ownership.

A second draft fix, [`Podtverzhdeno/hermes-agent#15`](https://github.com/Podtverzhdeno/hermes-agent/pull/15), makes cleanup **non-destructive for externally attached CDP**, preserving locally owned process cleanup. It may leave an agent-browser CDP proxy daemon pending a future ownership-proven disconnect; it never justifies killing a PID that has not been independently identified. This is **not evidence** the actual Windows runtime loads that dated fork.

An apparently closed browser can originate from **(A)** deleting the last MV3 task tab, **(B)** native Hermes idle/exit/explicit cleanup, or **(C)** Playwright/CDP ownership and process exit. These are separate root causes. Do not declare the incident fixed until the user's Windows process trace identifies the actual cause.

## 2. Implemented candidate changes

In `web-companion/background.js`, mirrored byte-for-byte to `web-extension/background.js`:

1. New **balanced** default: at most **6 currently active worker tabs** and **2 new job claims per poll**, staggered **2.5 seconds**. Existing bridge cap 25 and durable queued jobs remain unchanged. This is a resource **ceiling**, not a measured optimum.
2. Existing **max_parallel** remains explicit opt-in: **25 active** and **4 claims per 30-second poll**, staggered **1.2 seconds**. Inflight work is not aborted when the user changes profiles; the new cap governs future admissions only.
3. The full orphan-recovery scan is limited to once per **300 seconds** in MV3 `chrome.storage.session`. A new browser session scans immediately; the usual **30-second ACK/report polling is unchanged**. Storage survives service-worker suspension, not necessarily browser shutdown.
4. When the only normal window's last tab has an already authorized terminal result, **navigate that exact owned tab to `about:blank` instead of removing it**. An ordinary tab with siblings is removed as before. If the browser's state or tab identity is uncertain, preserve evidence rather than close a user tab. A parked blank tab consumes substantially less SPA activity than an open ChatGPT task; actual CPU gains remain unmeasured.
5. New popup dropdown selects balanced/max_parallel. No dynamic renderer killing, OS process killing, automatic login, cookie/profile copying, rerouting existing leases or resend on unknown outcome.

The distinction `done` vs `submitted` vs `unknown` remains intact. In particular, `submitted` is NOT a completed model answer. Completion-mode cleanup still requires a scoped SHA-bound `REPORT_ACKED`, accepted `/v1/result` and ownership verification. Failed or disputed bindings are preserved; lowering a concurrency cap does not force-remove any agent tab.

Known trade-offs: a five-minute scan can delay passive orphan recovery; explicit pause and `tab_removed` monitoring remain immediate. Full own-tab uniqueness scanning is intentionally retained before destructive completion cleanup, even though it can be costly; reducing those checks requires a new signed ownership ledger and live mutation/race QA, not a heuristic shortcut.

## 3. Recommended architecture: 100 logical jobs, 2–6 hot workers

```text
Human-authorized task backlog / per-agent durable ledger
      |
      +--> Queue admission control (fairness, dedup, max concurrency, stop)
               |
               v
        Edge profile supervisor   -----> health/metrics, immutable logs
               |
          2–6 live owned tabs (balanced)
               |
          one Send intent -> one attempted Send -> proof/ACK
               |
          retire to about:blank / close exact owned tab
               |
          next queued job; long-lived ledger independent of browser/tab
```

Never model `logical_agent == persistent_hydrated_tab`. Serial descendants can run indefinitely **only while there is real approved unfinished work and a valid quota**, but the total number of simultaneously executing requests must remain bounded. Zero work -> STOP/IDLE; no infinite meaningless "continue" loop. Use an explicit owner stop latch, fairness among roles, maximum per-role outstanding jobs, rate-aware exponential backoff and circuit breaker on authentication/DOM errors. No quota evasion or account switching.

State machine for one job:
`QUEUED -> CLAIMED -> TAB_OWNED -> SEND_INTENT -> SENT_OBSERVED -> REPORT_PENDING -> REPORT_ACKED -> DONE -> TAB_RELEASED`. Nonterminal branches `UNKNOWN_SEND`, `AUTH_MISMATCH`, `BINDING_DISPUTED`, `USER_CANCELLED` cannot automatically transition back to `QUEUED`. A timeout is not proof of non-delivery. The local report file is not independent proof of its writer's identity; the P085 host attestation requirement remains open.

### 3.1. Continuous Chain3 is not the same as 25 permanently running tabs

`chain3_continuous.py` already has independent, stricter protections. Its config validator requires `max_active <= 10`, `max_queued <= 4`, `min_claim_interval_seconds >= 60`, and `max_claims_per_utc_day <= 60`. A successor is gated by accepted prior report, immutable handoff and separately owner-armed campaign. These checks must not be removed to "speed up the infinite autopilot." The browser's 25-tab ceiling is a **different** protection; neither ceiling proves that 25 autonomous successors are simultaneously scheduled.

Its `ready()` walks completed generations from generation zero and revalidates predecessor artifacts, which can make each later refill increasingly expensive as an unbounded chain grows. This is a long-horizon algorithmic performance concern, not evidence that it currently dominates CPU. A future optimization could maintain a **protected, verifiable per-lane checkpoint** with exact accepted predecessor digests, and fall back to complete revalidation on conflict. Never trust an agent-authored cursor as authorization for a successor.

At review time the Chain3 CLI exposes `preview/arm/request/mode/select-mode`, not a dedicated owner-authenticated `disarm` command. The broader admission-hold mechanism exists; however an explicit **host-authorized STOP/Pause/Resume control** with durable audit and revocation semantics should be specified and tested before any unattended endurance mode. An agent-writable JSON "stop" file is not an independent authority boundary.

## 4. Microsoft Edge: minimal operational profile, **not a stripped security browser**

Recommended: **separate** `%LOCALAPPDATA%\HermesEdgeWorker` user-data-dir, not personal Edge profile, and only one loaded unpacked Web Companion extension. Run:

```powershell
# Preview the script in GitHub before executing. It does not kill processes.
.\Hermes-WebGPT\Start-Hermes-Edge.ps1
# Only if an explicitly selected local CDP transport is truly necessary:
.\Hermes-WebGPT\Start-Hermes-Edge.ps1 -EnableLocalCDP -CDPPort 9223
```

The launcher searches installed Edge, inspects existing `msedge.exe` command lines to avoid duplicate profiles, then uses only `--user-data-dir`, `--no-first-run`, `--no-default-browser-check` and `--new-window about:blank`. It does **not** remove Windows services, disable browser sandbox, site isolation, Safe Browsing/SmartScreen, networking, JavaScript, GPU or extension APIs. Those "optimizations" can reduce security, cause instability or break ChatGPT/extension readiness. There is no safe "all browser functions off" setting for an authenticated Javascript-heavy app.

The `-ConfigureHermesExecutable` switch only sets `AGENT_BROWSER_EXECUTABLE_PATH` for processes launched from the **same PowerShell process**; it does **not** make the existing Hermes agent-browser process attach to the already-running Edge profile. If Playwright/CDP is needed, inspect `connectOverCDP` versus `launch` and who owns `browser.close`/context lifecycle. Never make both the extension and CDP worker claim the same request.

Inside the dedicated Edge profile: use `edge://extensions` and install only the built Web Companion folder; sign in manually; inspect `edge://settings/system/managePerformance`, Edge Task Manager (Shift+Esc) and `edge://crashes` if available. Turn off unrelated startup/background features for this dedicated profile when operationally appropriate, but do **not** force-sleep, freeze or discard a tab with an active agent. Sleeping tabs and intensive background timer throttling can interrupt the page's timers, observer callbacks and result-gating. Preserve normal hardware acceleration unless measurements show a genuine GPU driver issue.

Remote debugging is off by default because its endpoint grants broad control of the profile; if enabled, restrict it to loopback, never port-forward it, never expose it to another machine and do not reuse personal profiles.

### 4.1. One active browser client per bridge

Migration from Yandex/Chrome to Edge requires a **quiescent handover**. The old browser extension and the new Edge extension must **not simultaneously claim jobs** from the same loopback bridge. Each extension has its own MV3 `storage.session` and tab IDs, while the bridge's 0.6.6 heartbeat checks only version/state/window count, not cryptographic browser identity. Concurrent clients can race to consume queue items, complicate recovery, inflate CPU and make process/window evidence misleading.

Before enabling Edge worker polling: pause admissions; record exact queue/active/unknown job counts; reconcile any uncertain Send; stop only the **old extension's polling** by user-approved disable/reload (do not delete its profile or close uncertain tabs); then enable Edge once, verify bridge heartbeat and a **single deliberate dry-run**. Browser process presence alone is not evidence that the correct extension version is running. This handover should eventually use host-attested single-client leases/fencing, but an agent-writable local flag is not sufficient identity proof.



## 5. CPU model and measurements — no invented percentage

```text
CPU_total ~ CPU_idle_Edge + active_tabs * CPU_per_hot_tab
            + hydration_burst_rate * CPU_hydration
            + recovery_scans * CPU_scan + extension/bridge overhead
```

The relationship is workload-dependent and not exactly linear. Test at **1, 2, 6, 12, 25** tabs with the same task prompt and browser/version on AC power, recording: 60-second median and p95 `msedge.exe` CPU percentage normalized by logical processor count, peak working set, process tree count, tab task-manager CPU, `executeScript` calls per minute, claim-to-send p95, ACK-to-close p95, actual completed-job throughput, and number of lost/ambiguous jobs. Repeat with same warmed profile; collect three trials per condition. Report confidence intervals rather than assume an arbitrary 40–70% saving.

If CPU stays high at two tabs, profile/renderer/extension overhead is the bottleneck; removing more tabs may not fix it. If CPU rises only at 12/25, the bounded worker pool is the correct first lever. If GPU or power usage spikes, test graphics acceleration A/B without disabling security features.

Operational fail-safe: a CPU or RAM alarm should stop **new claims**, not kill tabs with uncertain sends, delete browser processes, clear session state, or report completion. When system resources recover, an explicit authorized supervisor can resume admissions without replaying already-claimed work.

## 6. High-risk compatibility requirements

* Keep the local bridge authentication secret in local config only; never commit `bridge-config.json`, bearer tokens, cookies, browser profile, local SQLite or user prompts.
* A code SHA, extension manifest version, installed unpacked files, active MV3 worker, bridge heartbeat and native Gateway process are distinct. The bridge currently demands extension version `0.6.6` for its compatibility gate. This candidate keeps that manifest string; **a production release must coordinate a versioned bridge/extension rollout and verify loaded code identity** rather than silently treating a source edit as an installed version.
* Roll out only after a current work queue snapshot, targeted backup, verified empty active lane where possible, and manual extension reload. Do not delete/requeue old uncertain tasks to make the status appear clean.
* Existing real-browser live smoke evidence for older releases does not validate the current changed sources or the Microsoft Edge process. A Windows/Edge live E2E remains required.

## 7. Acceptance gates / negative tests

| Test | Required result |
|---|---|
| Only managed task tab in only window; verified terminal receipt | Edge remains visible; tab becomes `about:blank`; no second Send |
| Managed tab has another tab; verified terminal receipt | Only exact managed tab closes; user tab remains |
| Browser executable exits after startup | Process/PID/exit/crash evidence distinguishes browser shutdown from final-tab closure |
| 20 queued jobs, balanced selected | No more than 6 worker tabs; remaining backlog stays durable |
| 30 queued jobs, max_parallel selected | Never more than 25 worker tabs, 4 new per poll |
| Two polls 30 seconds apart | ACK polls run; no duplicate full DOM recovery scan |
| MV3 service worker restarts | Durable state restored safely; no job is sent again |
| ACK missing, forged, stale, wrong job, duplicate URL | No completion and no tab closure |
| User changes to a different account; session is logged out | New admissions paused; uncertain send not retried |
| Temporary Chat has no durable `/c/` URL | Do not claim it passed ordinary-chat completion tests |
| Disable CPU policy changes while 25 jobs active | Inflight jobs preserved, no forced close |
| Eight-hour endurance with 100 authorized tasks | No unexpected browser shutdown, no duplicate sends, bounded RAM/CPU, no unexplained lost work |

The first three simulated acceptance cases and a balanced/max-parallel admission scenario have been exercised against the patched service worker with mocked browser APIs. This is **not a Windows process or actual ChatGPT UI E2E test**. The target-side Node and Python regression suite and real-browser endurance test are still necessary.

## 8. Recommended long-term direction

For a production-grade unattended orchestrator, use the **official API** and a durable external queue, explicit task ownership, request IDs, backpressure and quotas. Browser automation through the consumer ChatGPT website is fragile and subject to its terms; OpenAI's consumer Terms prohibit automated/programmatic extraction of Output and bypassing rate limits. Do not design an infinite web-UI loop that attempts to circumvent those restrictions.

For a developer-controlled official API worker pool, Edge can become a **monitoring dashboard only**, not a compute prerequisite: a browser crash will not lose the task ledger and tabs no longer dominate CPU. Obsidian/GitHub can hold curated project artifacts with narrow read/write permissions, but never use a single agent-authored report as independent quality verification.

## 9. Rollback

This proposal lives only on the `hermes/edge-resource-supervisor-20261122` branch until reviewed. The original `main` source and live Windows installation are untouched. To undo, close the PR/ignore the branch. For a later owner-approved local installation, retain a backup of **exact** original extension files, compare SHA, replace only known source files with an empty queue, reload once, verify heartbeat/version/runtime build and re-run isolated smoke tests. Never restore/delete `bridge-state` as part of a code rollback.
