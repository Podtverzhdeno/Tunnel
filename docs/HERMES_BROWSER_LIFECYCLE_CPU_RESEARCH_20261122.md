# Hermes / Web Companion — lifecycle & CPU audit (2026-11-22)

> Status: code audit + isolated patch on branch. **Not a live Windows fix**, not deployed.
> Repository source examined: `chat-launch-smoke-desktop-test/web-extension/background.js`, manifest v0.6.6.
> Historical README distinguishes GitHub 0.6.6, portable ZIP 0.8.5 and deferred 0.9.7. Never assume the installed extension equals this tree.
> Never clear browser storage, change an installed extension ID/profile, release admission holds, or resend uncertain jobs as part of this patch.

## 1. Findings supported by inspected code

1. The extension does **not** invoke `chrome.windows.remove()`, kill the Edge process, or launch a new browser. It uses `chrome.tabs.create({active:false})` and two paths using `chrome.tabs.remove()`.
2. Previously, those paths could remove a task tab even if it was the **last tab in its window**, making the browser window disappear. This is a plausible explanation for the reported visual symptom **if the task window has only that tab**; it is not established as the user's actual cause.
3. The MV3 alarm is scheduled every **30 seconds**. Before this patch every `poll()` invokes `recoverOrphanedTabs()`, which fetches storage, enumerates all tabs, and may execute ownership probes in saved ChatGPT pages.
4. The existing admission limits are `CAP=25` and `CLAIMS_PER_POLL=4`, not a per-machine CPU-aware budget. Up to 25 hydrated tabs can dominate CPU independently of the extension's own poll cost.
5. The strict `verifiedOwnedTab()` procedure verifies exact saved route, DOM marker and uniqueness before completion-related closure. **Do not remove these checks** to make polling faster: they prevent closing a user-owned tab.
6. Existing unknown-send and report-ACK holds are intentional safeguards. This patch does not change report receipt semantics or retry policy.

## 2. Narrow code change in this branch

**A. Last-tab window protection.** `hasAnotherTabInWindow(tabId)` checks that there is at least one other tab in the same window before either completion-driven `chrome.tabs.remove()` call. If a tab is the last tab, cleanup is skipped; in completion mode the accepted receipt and local ownership evidence remain for a later close-only retry. This is a **best-effort** check, since a sibling may close between query and remove. An atomic compare-and-remove API does not exist.

**B. Recovery scan throttle.** A session-scoped `chatSmokeRecoveryScanAt` key reduces repeated full orphan scans to no more than once in 120 seconds during normal conditions. A fresh browser session clears `chrome.storage.session`, restoring boot-time recovery. Failed scans are not marked successful. The theoretical number of *scan invocations* is 120 → 30 per hour in steady state; this is **not** a 75% CPU-reduction claim. The important independent ownership verification on actual closure remains unthrottled.

**C. Separate tests.** Regression tests assert safe behavior for one-tab, two-tab, different-window, gone-tab and unavailable-query cases, and recovery interval semantics. The workflow runs them against the actual `background.js`, without contacting ChatGPT.

No change to `CAP=25`, claims per poll, authentication, ChatGPT transport, browser profile, report ACK, existing tasks, or account behavior was made: choosing a new cap requires baseline measurements and reconciliation with bridge admission rules.

## 3. Root-cause decision table

| Observation | Candidate mechanism | Conclusive evidence | Safe repair |
|---|---|---|---|
| Only the last browser window vanishes immediately after agent task completion | `chrome.tabs.remove` on its last tab | Event order + per-window tab count + browser remains without other windows | Current guard; close-only retry, do not create new window silently |
| All browser windows close when launching script exits | Playwright-owned browser context or Windows Job Object | Process creation tree, exact launcher code, Job membership; reproduce with no ChatGPT navigation | Durable browser owner; don't close persistent context from worker; proper Job handling |
| New Edge process immediately exits but existing instance stays | Reused `--user-data-dir` singleton handoff | PID + exact profile path and existing instance | Dedicated profile + single owner; do not delete live lock files |
| UI remains but agent stops advancing | MV3 worker idle shutdown, tab sleep/freeze/discard, unavailable bridge | Extension service worker event logs; `edge://discards`; bridge heartbeat | Persistent checkpoints, event-driven worker, distinguish active from sleeping |
| CPU increases after 4→25 ChatGPT tabs | Per-tab renderer JS, heavy content scripts, repeated DOM queries, extension scan | Edge Shift+Esc, per-process CPU and memory, Performance traces, active tab count | Queue + bounded active pool; do not disable renderer sandbox |
| Browser closes while tab count >1, no extension removal logged | External process supervisor, crash, user/OS close | Windows Application event, WER crash, Sysinternals Job membership | Fix true owner or crash; avoid speculative flag modifications |

## 4. Windows one-line investigation commands (read-only)

On the computer actually running Hermes, first identify **which source revision is installed**. Use Edge/Yandex extension manager to compare active version and unpacked source path. Do **not** reload or replace an extension during uncertain jobs.

From PowerShell in an intentionally downloaded engineering bundle:

`powershell -NoProfile -File .\windows\Collect-HermesDiagnostics.ps1 -DurationSeconds 180 -IntervalSeconds 2`

If a dedicated **test-only** Edge profile is used:

`powershell -NoProfile -File .\windows\Monitor-HermesBrowserLifecycle.ps1 -DurationSeconds 180 -IntervalSeconds 2`

For browser tabs: Edge `Shift+Esc` (Browser Task Manager); `edge://discards`; `edge://crashes`; `edge://policy`. These are diagnostics, not replacement for actual CPU and memory time-series. Avoid sharing raw process arguments, personal paths or browser profiles.

## 5. Controlled Edge performance experiment

Before replacing an existing Yandex/Chrome profile or disabling settings, measure A/B on the same Windows PC and Windows power plan. For each configuration test cold and warm runs at 1/2/4/8 active sessions, never exceeding provider limits:

- Browser version/channel, extension revision, profile, physical/logical core count and RAM.
- Browser master PID, process count, foreground/background/occluded state, exact count of active vs parked tabs.
- Aggregate browser CPU-seconds / task, CPU % total logical capacity, RAM RSS/commit, p95 response and success/failure.
- Independently validate `report ACK → correct job` and no repeat Send; samples without independently confirmed completion are not throughput successes.
- First 2 minutes are warmup; record 10–30 minutes steady state; 24-hour soak after functional tests.
- Compare default Edge with normal sleeping tabs to a *dedicated clean Edge profile*, not a globally weakened Edge with disabled sandbox/SmartScreen/updates/site isolation.
- Treat ChatGPT web UI as a non-stable interactive interface; for sustained programmatic orchestration prefer supported API transports and explicit rate limits.

### Metrics gates (acceptance **targets**, not achieved results)

1. Zero browser-window closures caused by the extension's own cleanup when only one tab remains.
2. Full recovery scan no more than once per 120s in healthy session (unless session storage resets).
3. No new externally visible Send when outcome is uncertain.
4. A/B CPU-seconds per confirmed unit of work and memory peak do not regress, and p95 latency remains acceptable.
5. No loss of queue state across worker/browser restart; 24h run has zero unexplained browser exits, with comparable workload.

## 6. Reference documentation and research corrections

- Playwright Browser.close: https://playwright.dev/docs/api/class-browser — launched browser closes; connected browser disconnects. `launchPersistentContext` closing its sole context closes its browser.
- Playwright BrowserType: https://playwright.dev/docs/api/class-browsertype
- Chrome MV3 SW: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/ — in-memory globals are not durable, idle termination is normal.
- Chrome 136 debugging profile: https://developer.chrome.com/blog/remote-debugging-port — custom user-data-dir required for Chrome's default-profile debugging restriction; independently check Edge behavior.
- Microsoft Edge Sleeping Tabs: https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/sleepingtabsenabled
- Windows Job Objects: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- Edge CPU diagnostics: https://learn.microsoft.com/en-us/troubleshoot/microsoft-edge/performance/edge-high-cpu-memory

**Caution about some external research recommendations:** `--in-process-gpu`, `--process-per-site`, blanket disabling timer throttling, minimizing all windows and global `SleepingTabsBlockedForUrls` are not validated CPU optimizations for this project. Such changes may increase load, break isolation or stall UI-dependent flows. Keep Chromium's defaults until controlled measurements demonstrate otherwise.

## 7. Release restrictions

This branch is intentionally a **draft**. Before merging or distributing the extension:

1. Review source/installed version differences and bridge heartbeat exact-version gates; do not silently label modified code as unchanged production version.
2. Run Node regression and existing Python/JS bridge tests.
3. Run 1-tab and 2-tab Edge/YaBrowser smoke in a **throwaway profile**, never on outstanding real jobs.
4. Verify that a preserved last tab with an accepted report is eventually closeable after the user opens another tab, with no duplicate receipt.
5. Confirm `storage.session` reset and orphan rescan after an actual browser restart.
6. Roll out to the live installation only after owner review of uncertain jobs and an explicit upgrade plan.

No current Windows session or live browser was controlled or modified by this change.
