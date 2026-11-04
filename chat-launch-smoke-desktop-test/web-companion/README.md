# ChatGPT New Chat Smoke — Web Companion v0.6.6

## v0.6.6: composer recovery without unsafe retries (2026-11-03)

**Root cause:** v0.6.5 checked only the first top-level Send selector match, so disabled/hidden responsive duplicates, shadow-root controls, or unrelated buttons could make the correct editor appear unsendable. Two pre-send failures also latched the entire queue permanently even when other jobs successfully saved ChatGPT messages.

**Corrections implemented in both `web-companion/` and the installed unpacked `web-extension/`:**

- Active composer-anchored Send detection across open shadow roots; reject unrelated forms and ambiguous same-score controls. No blind Enter fallback.
- Failures emit bounded typed `send_ui_diagnostic` reasons; no prompt, email, cookies, personal account identity or transcript in diagnostic telemetry.
- UI pre-send failures trigger a 90-second initial cooldown instead of permanent `paused`; after cooldown allow exactly one **new** campaign probe. Successful `message_saved` at a canonical `/c/` URL resets UI failure state. Two failed probes or an ambiguous probe result stop admissions until operator review. Account-context errors remain a hard persistent pause.
- Pre-send failures are marked `needsReconciliation`, not `DONE`, and no old job is retried or requeued. Existing SHA-bound report ACK, close-only retry and identity safeguards remain unchanged.
- The bridge requires a fresh `0.6.6` heartbeat before new completion-mode claims. Rollout HOLD prevents new claims during the manual browser extension reload.
- Verified: 34 selector assertions; JS tests for send, circuit cooldown, probe budget, ownership and closure PASS; 131 Python tests PASS, including two new compatibility/HTTP telemetry checks.

**Required deployment:** In the **existing YaBrowser profile** with extension ID `oblnogbmakejdlcjojogopnbiglbkmfl`, open `browser://extensions` and click **Reload / Обновить** on the installed unpacked extension. **Do not delete or reinstall it**, clear its storage, close unknown tabs, or repeat P108/P148 and four `send_button_missing` jobs. Run `python v066_rollout_gate.py` for a read-only status check. Only after the new extension reports fresh `version=0.6.6, state=polling` and the operator confirms reloading, run `python v066_rollout_gate.py --apply --owner-confirmed-browser-reload` to archive only the upgrade hold and allow new *different* jobs.

**Live status before reload:** the installed browser worker still reports `0.6.5`; new admissions intentionally remain blocked. Unit tests do not prove a live ChatGPT send-button repair until a version `0.6.6` real job is sent and independently ACKed.

## Historical v0.6.5 recovery notes


## v0.6.5: recovery after the real four-agent trial

The live repeat-v064 browser trial reached **4/4 sent and saved ChatGPT
messages** and produced **4/4 SHA-matched immutable report ACKs**, but
v0.6.4 preserved all four tabs with `tab_identity_unverified` because
ChatGPT's current user-message DOM did not satisfy the strict prior selector.
This is an observed browser failure, not just an offline regression.

v0.6.5 provides a bounded DOM fallback **only if** the extension previously
persisted a unique agent UUID + exact canonical saved conversation URL,
the visible conversation contains the known launcher prompt prefix, the
editor is no longer holding the unsent prompt, and the server confirms the
job's recorded send/save. Duplicate URL or conflicting visible UUID fails
closed. A DOM-only orphan **without** a previously saved local ownership
record cannot use this bounded fallback.

The bridge and client now strip URL query parameters before persisting
completion results; invalid hosts, paths and fragments are rejected.
Failed browser tab removal preserves the receipt and local ownership data
and retries **close only**, without resending `/v1/result`. If MV3 restarts
after the server already committed DONE, `/v1/close-eligible` revalidates
the report bytes against the job's SHA/ACK and returns the original
canonical URL; the client requires an unchanged persisted ownership binding
and fresh unique DOM verification before removing the tab. No prior prompt
is resent.

**Rollout:** The updated unpacked files are already placed in
`chat-launch-smoke-desktop-test/web-extension`. Reload the unpacked
extension in the **same existing browser profile** (browser://extensions).
Verify with `python status.py` that `extension.version=0.6.5`, heartbeat is
fresh, and the new recovery scan identifies and restores the four old jobs.
Keep legacy `admission-hold.json` intact. Do not manually close old
tabs or retry old job IDs. Final success requires observed
`receipt_accepted`, `tab_closed` and server DONE for each live job,
not merely source tests or ACK files.

Known limitation: the browser API cannot atomically compare a tab URL and
close it; checking twice reduces the chance of navigation races but does
not eliminate it. DOM-based ownership also does not establish a
cryptographically authenticated account identity.



Local Windows MCP broker + browser extension for bounded, user-authorized experiments with ordinary ChatGPT chats. This is NOT a native official multi-chat API. It requires a running, logged-in browser with the unpacked extension installed. It does not bypass ChatGPT limits.

## Role-based substantial engineering campaigns (controller v1)

A new finite role-campaign scheduler is available in role_campaign.py with
25 unique engineering features x 3 stages: FEATURE -> DEVELOPER -> REVIEWER,
75 potentially substantive assignments. Each stage has acceptance criteria,
negative cases, real tests and strict workspace isolation. REVIEWER independently
checks developer output. Stages depend on verified server DONE plus report SHA ACK,
not self-reported completion. No new agents are allowed to spawn agents.

The authenticated bridge can refill a *user-armed* campaign's queue
(up to 4 waiting jobs, 25 active ceiling), with deterministic UUIDs, exact role
metadata and immutable brief digests. Claim-time validation rechecks the active
owner arm and dependency. An unknown/error result never automatically retries.

**Not active for the existing experiment:** the admission hold is still present
and 17 old active + 8 queued jobs must be reconciled. The campaign is disarmed.
Run python role_campaign.py preview for the read-only backlog, and consult
ROLE_CAMPAIGNS.md. Do not arm until old work is reconciled, the local bridge
is safely updated, and an independent live completion+review smoke succeeds.
The browser extension does not need a version update for this server feature.

## Chain2: DEVELOPER ↔ REVIEWER, one end-of-task handoff

New two-role opt-in controller: `chain_campaign.py` with
`role-campaigns/chain2.json`. It supersedes the unused 3-role eng1 design
for future workload experiments but does not alter legacy work or permissions.

- Only DEVELOPER and independent REVIEWER. Feature proposals are collected in
  the handoff and reviewed; they are never automatically started.
- No successor request at the beginning. Near the end, the worker writes
  `handoff.json`, uses connected Local Workspace MCP to invoke
  `chain_campaign.py request --parent <JOB_ID>` exactly once, checks its
  actual job result, then writes its final report.
- Request marker is immutable; new chat is **not** created until its parent is
  server DONE, with matching report ACK and unchanged handoff. The bridge
  refuses report ACK until the request exists.
- Each successor receives its predecessor's handoff path, including an
  `upstream_handoff` pointer for older context. The finite 25-ticket OpenSpec/SDD
  continuation backlog has 50 role tasks and ten root lanes; its first ten
  tasks reference the research reports from the previous ten-agent batch.
  Terminal roles cannot invent work.
- `python chain_campaign.py preview` and `python chain_metrics.py` are
  read-only. Arm is explicitly gated by owner confirmation, a healthy browser,
  zero legacy active/queued jobs and no admission hold.

See `CHAIN2_RUNBOOK.md` for protocol, tests and deployment blockers.
**Chain2 status (2026-11-03):** owner-armed for 10 SDD continuation lanes (max 50 finite stages), with 127/127 Python regression tests passing. After 8 root jobs were claimed, the browser extension reported `account_context_changed` on one potentially sent job and entered `paused`. A new `admission-hold.json` stops further claims while existing report ACK/closure remains possible; the two remaining root jobs stay queued. Operator must reconcile browser account identity and uncertain send status before hold release, without replay. No merge or host identity approval is implied.

## Durable task-to-tab ownership (v0.6.4)

The unpacked Chrome/YaBrowser extension now persists one privacy-limited
`chatSmokeOwned_<job UUID>` binding in `chrome.storage.local` per completion
job, with the original tab ID, **canonical saved ChatGPT conversation path**
(no query parameters), startup timestamp and reconciliation flag. It does
**not** persist the prompt, conversation body, account, cookies or bridge token.
Each binding is saved independently so concurrent agents do not overwrite
each other's map. Once a verified result is accepted, the local binding is
deleted. A prior unexpected close remains quarantined, not retried.

After browser or extension restart, the worker rescans actual ChatGPT tabs.
A persistent binding is a **hint, never proof**. Reattachment requires a
unique matching user-message marker, a saved conversation path matching any
persisted path, and `GET /v1/recover` eligibility from the bridge. Tampered
or disputed bindings, reused tab IDs, changed conversations and duplicated
agent chats fail closed. An originally bound agent also undergoes **fresh DOM
identity and duplicate detection before result submission and immediately
before browser tab removal**. A recovered job never exposes its original
prompt through `chat-smoke-get`, so nothing is resent.

New count-only scan telemetry includes `stored_owned`, `stored_invalid`,
`stored_conflict`, `restored_from_local`, `recovered_dom_only`.
The broker accepts old scan-only data during a rolling extension upgrade,
but will not admit new completion jobs until the **v0.6.4** heartbeat is fresh.

**Apply update:** From this project, `python build_clean_extension.py`
refreshes the allowlisted unpacked files. Then manually reload the extension
in `browser://extensions` in the browser profile holding the agent chats.
Confirm `python status.py` shows `extension.version=0.6.4`, `state=polling`
and a fresh heartbeat, followed by a new scan diagnostics timestamp.
**Do not remove the existing admission hold** or bulk-close legacy tabs.
v0.6.4 cannot retroactively recover agent tabs that were closed or are absent
from the profile accessible to the extension; existing old jobs without local
bindings still require exact unique DOM evidence and server eligibility.

## Architecture / failure semantics

- Local Workspace MCP writes approved tasks to a durable queue. The loopback broker on `127.0.0.1:18763` exposes authenticated claim, receipt, heartbeat and small diagnostic events.
- The Chrome/YaBrowser MV3 service worker opens at most **25 inactive tabs at once** in an existing browser window, with no more than **4 new claims per 30-second polling cycle** to limit browser hydration spikes. After two consecutive pre-send failures it pauses further claims. The extension popup displays the paused state and offers an explicit Resume control. It opens tabs ~1.2 seconds apart rather than launching ten simultaneously.
- Each tab requests its own task by ID, attempts one send, confirms the message on a saved conversation URL, and returns a receipt. Independent task storage prevents one tab from overwriting another.
- The content script waits for editor hydration and, if the send button remains disabled, re-syncs the editor **before** the click; never repeats a clicked send. Prompts for nine recovery jobs are short and reference separately stored full briefs.
- A task result is **done** only when a completed assistant reply is observed, **submitted** when the user's message was saved but assistant completion is not verified, and **unknown** for uncertain delivery. No automatic retry of unknown/possibly sent messages.
- Captured completed answer text (up to 8,000 characters) is stored in a scoped local `agent-reports/chat-JOBID.md`; a new chat can also write its own `agent-reports/ROLE.md` via shared MCP. Neither is proof that MCP was actually used unless tool receipts are verified.
- Research tabs have hold `minHoldSeconds=180`. Even this can interrupt long outputs: tab closure is NOT a promise of continued execution.
- Browser heartbeat reports version and number of windows. Task diagnostic events include stage and fixed reason codes only, not prompts, tokens, cookies, or prior conversations.

## Applying an update

1. Open **Yandex Browser** → address bar `browser://extensions` (NOT Windows CMD).
2. Reload **ChatGPT New Chat Smoke**. The unpacked extension source is already in `C:\\Users\\EXAMPLE\\IdeaProjects\\chat-launch-smoke-desktop-test\\web-extension`.
3. Verify `python status.py` via Local Workspace MCP shows `extension.version=0.6.4` and heartbeat age under 120s.
4. Restart the broker with `python restart_bridge.py` from the companion folder for server-side changes, only after verifying the active queue is empty. The script verifies the exact local process before stopping it.

## Nine-job recovery — NOT YET AUTOMATICALLY SENT

One prior bounded batch opened ten tabs. Nine tasks failed **before send** with `send_button_missing`; only host-identity had a saved conversation, with reply completion unconfirmed. `prepare_retry.py` independently checks original job receipts and events and produces exactly nine short prompts in `retry_nine_plan.json`, plus original full briefs in `../agent-briefs`.

`python enqueue_retry.py --dry-run` validates the nine-job plan. Only the deliberate `python enqueue_retry.py` dispatches tasks, and only with live v0.4.0 heartbeat and empty active/queued state. It is idempotent using a fixed batch marker. `python recovery_status.py` shows per-job queue/active/done/submitted/unknown, chat links and reports.

Do not silently rerun an unknown task: always reconcile the original evidence. This experiment's descendant-agent budget is zero; new chats must not spawn other chats.

## Completion-gated research mode (v0.5.0)

This is an **opt-in mode**. Existing one-shot smoke tasks keep their
previous semantics. No autonomous descendants or attempts to extend model
runtime/provider quotas are enabled.

- The human-authorized controller runs `enqueue_completion.py --role ROLE --brief agent-briefs/ROLE.md` after writing the brief. It creates a unique job ID and task-owned report name.
- A newly opened ChatGPT agent receives the exact job ID and a request to write its result through the shared Local Workspace MCP to `chat-launch-smoke-desktop-test/agent-reports/completion-JOBID.md`, with `Job ID: JOBID`, `## Outcome` and `## Evidence` sections.
- The content script sends a `message_saved` event with the persisted chat URL, but **never** declares the job complete merely from a visible assistant answer or an elapsed 180 seconds.
- Every 30-second browser poll asks the authenticated loopback bridge `GET /v1/ack?id=JOBID`. It checks only the exact task-owned regular report file, minimum/maximum size, UTF-8, content headings, matching job ID and the actual SHA-256. The bridge persists an immutable advisory ACK.
- **Compatibility admission gate:** the bridge will not hand a completion-gated job to any browser with a missing, stale (older than 120 seconds), paused, or non-v0.6.4 heartbeat. It returns `job:null` and `reason:completion_client_not_ready`, leaving the job safely queued. Reload the unpacked extension and check `status.py` before retrying any rollout. Heartbeats are compatibility diagnostics, not cryptographic proof of browser or account identity.
- The server then requires that exact `ack_id` before accepting `/v1/result`. Only after the result is confirmed does the extension close the exact tab ID **and matching saved conversation URL**.
- A missing report keeps the tab open. After a watchdog interval the system records `report_watchdog` but does **not** resend or close. If a tab disappears, it is quarantined as `needsReconciliation`. Never retry a possibly sent prompt automatically.
- See `status.py` for `completion_mode`, `report_exists`, `report_ack_recorded`, and the final `report_ack_id`/SHA. No prompt/cookie/token is included in that diagnostic output.

**Trust boundary:** the bridge's ACK proves integrity and binding of a
file *delivered through the local MCP workspace*. This is **not**
independent quality acceptance or authenticated Windows actor identity.
An agent capable of writing its own file can self-submit that report.
Full production approval still requires P085 and the owner-reviewed
OpenSpec/SDD route. The previously built isolated receipt-store and
browser-gate prototypes remain under `experiments/completion-gated`
until production owner/reviewer authorization.

This mode's browser automation must be reloaded to v0.5.0 before a real
end-to-end test. Local Python and Node mock tests do not verify ChatGPT's
live DOM or account/session state.

## Account-context mismatch: diagnosed safeguards (v0.4.2)

In task `b7644bd6-ec96-5743-81ff-86a190f55882` (independent
completion integration QA), telemetry shows `send_clicked` at 21:41:47 UTC,
`message_saved` at 21:41:55 UTC, then `tab_closed:unexpected_close` at
21:41:58 UTC. The receipt is `unknown` and has no saved conversation URL.
The operator reported ChatGPT's "account changed" message and manually
resubmitted a prompt. **The exact cause of ChatGPT's account notification is
not observable in the old logs.** In particular, these logs cannot prove an
actual account login switch, and the extension has no account-switch API.

v0.4.2 detects explicitly worded account-change notices **in alert/status/
dialog/toast UI only**, without collecting names, emails, cookies or login
tokens. When detected before or after click, it records
`account_context_changed`, marks `unknown`, pauses all new dispatch
persistently, and preserves the affected tab for manual reconciliation.
It does NOT click Send again. A user can verify their signed-in identity
and manually resume from the extension popup; this does not retroactively
change the failed result. The detection is selector-based and may miss a
new UI presentation, so it is not proof of fixed backend authentication.

For long research prompts without an explicit hold, fallback observation
is 180 seconds rather than the 5-second smoke timeout. **180 seconds is
still not completion-gated ACK.** The formal design for that functionality
remains in a separate pending owner-approved OpenSpec request.

## 25-agent parallel admission (v0.6.0)

- Maximum **25 ACTIVE** bridge jobs and **25 temporary background tabs**. An independent admission cap also allows up to **25 queued** research jobs. Setting these limits does **not** enqueue 25 jobs, extend model thinking budgets, or waive ChatGPT usage limits.
- The browser admits at most **4 new jobs per poll** (30-second alarm), paced ~1.2 seconds apart. This prevents a 25-tab burst and limits MV3 service-worker execution time. Capacity refills as acknowledged tasks close; unknown/missing-report jobs continue occupying their slots until reconciled.
- Existing account-change pauses, exactly-once send, immutable bound-report ACK, and no-automatic-resend safeguards remain in force. Memory, browser tab discarding, authentication prompts, and network quotas can reduce practical concurrency.
- Update the unpacked extension via `build_clean_extension.py`, restart the bridge when no active jobs exist, then **manually reload the extension** at `browser://extensions` and verify the new `0.6.0` heartbeat. An old `0.5.0` extension cannot claim completion-gated jobs against the updated bridge.
- Validate with `python -m unittest discover -p "test_*.py" -v` and `node test_background.js`, plus a staged live smoke before loading 25 genuinely complex tasks.

## Recovery of orphaned agent tabs (v0.6.1)

- During an extension reload, in-memory task↔tab mappings may disappear while agent chats remain open. The worker now scans saved ChatGPT conversation tabs and matches ONLY prompts with the exact agent-launch introduction, the expected MCP alias instruction, and one matching task-specific report filename + UUID.
- A new authenticated `GET /v1/recover?id=UUID` route authorizes recovery only for an active completion-mode job with observed `send_clicked` followed by `message_saved`, and without unknown, account-conflict, or unexpected-close telemetry.
- Recovered tabs are not marked done. Their report still needs the bridge's immutable, SHA-matched `REPORT_ACKED` and an accepted `/v1/result` before `chrome.tabs.remove` is called for that exact tab ID and exact URL.
- Duplicate matching chats, normal user chats, malformed URLs, missing reports, disputed ACKs, and ambiguous or failed jobs remain open for manual reconciliation. The extension never retries a possibly sent prompt.
- Recovery requires the MV3 `scripting` permission and explicit `https://chatgpt.com/*` host permission to inspect existing tabs after reload. It returns only the job UUID, not chat text or account details.
- Do not remove `bridge-state/admission-hold.json` while draining this stressed batch: it blocks new claims without preventing ACK checks or result acceptance. This hold is an operator safety switch.
- Apply with `build_clean_extension.py`, restart the bridge, and manually reload the unpacked extension at `browser://extensions`. Some browsers require approving the additional ChatGPT site permission. Verify the extension's fresh `0.6.1` heartbeat and read-only status; do not claim tabs closed until live telemetry confirms.
- Existing active ChatGPT generation may not survive browser crashes or reloads. Recovery is a best-effort local integrity path, not evidence of trusted actor identity or model thinking duration.

## Recovery diagnostics (v0.6.2)

- Offline verification established 12 immutable job-bound report ACKs in the 25-agent test, but the extension v0.6.1 did not emit tab_recovered or tab_closed telemetry. Do not claim that a successful ACK alone closes tabs.
- Every 30-second poll in v0.6.2 scans tab metadata through `chrome.tabs.query({})`, inspects only matching `https://chatgpt.com/c/...` pages, skips discarded tabs, and attempts restoration exclusively for an exact uniquely identified user-launched agent chat. Only after server recovery eligibility and bound report ACK is the exact owned tab allowed to close.
- Local diagnostic `bridge-state/diagnostics/recovery-scan.json` contains ONLY bounded aggregate counts: total tabs, ChatGPT chat tabs, discarded, injected, identified, eligible, recovered, ambiguous, injection failures. No tab titles, URLs, prompts, account data or tokens. If the count of discarded tabs is nonzero, the browser may have suspended the original conversations and a safe reattachment may require their manual reactivation.
- The admission hold remains active so pending jobs are not released while recovering existing results. The five pre-send failures remain quarantined for manual reconciliation.
- After the unpacked extension reload and fresh v0.6.2 heartbeat, inspect this diagnostic file and the per-job `tab_recovered`, `receipt_accepted`, and `tab_closed` events. A tab closure is not complete without `tab_closed` telemetry and a matching done record.

## v0.6.3 diagnostics after confirmed browser reload

The v0.6.2 real-browser scan showed **17 total tabs, only one ChatGPT saved /c/ conversation tab, zero agent identifications**. Therefore there is no verified basis to claim the 12 report-acked agent tabs were still open or to close any random existing window. Report ACKs exist but no tab_closed telemetry.

v0.6.3 diagnoses the remaining 16 tabs by **aggregate categories only**: saved /c/ ChatGPT pages; other chatgpt.com pages; non-ChatGPT tabs; unavailable URL. It scans **all** chatgpt.com pages for the exact task-owned user-message marker, but still requires a stable saved /c/ conversation URL, unique job ID, server recovery eligibility, ACK, and exact tab URL before closing. For an agent message on an unsaved ChatGPT route it records unsaved_identified instead of closing. For non-ChatGPT tabs it does not inspect page contents.

Read `bridge-state/diagnostics/recovery-scan.json` only after browser reports 0.6.3. If the 16 tabs are non-ChatGPT, they cannot be assumed to be left-behind agent tabs. The system does not have a remote debugging port into the user's browser and must not close unknown user tabs. If no safe match is possible, ask the user to select the actual agent tabs for targeted cleanup instead of expanding deletion criteria.

## Security and limits

- The service binds only to loopback and requires a random locally held bearer key.
- Do not share `bridge-config.json` or `bridge-state/token`. The source package intentionally excludes these.
- ChatGPT might change its DOM; a successful local JS mock test does not prove real-browser delivery.
- Unauthenticated browser chat history listing, remote-tab readout, production isolation of agents, and guaranteed continuous generation are **not** implemented.
- The general Local Workspace MCP has broad privileges: task prompts do not establish trusted Windows identity, OS isolation or owner approval. P085 remains open.
