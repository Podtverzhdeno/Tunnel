# Continuous execution — design (draft)

## Existing components
- Hermes Relay: request_id, lease, noReplay, queue, runtime observability. It owns Send state.
- Web Companion: browser profile/tab/turn evidence; owns only its acknowledged browser interaction, not Telegram delivery.
- AutoPilot: execution plan, task dependencies, 1–5 lane UI, pause/stop. Current candidate is SAFE_DEMO with automatic Send disabled.
- Telegram Gateway: final delivery and Bot API message ID evidence, separate from Relay.done.
- File Broker: optional output/input artifacts. A lane with unresolved media upload/download/Telegram receipt is not eligible for silent rotation.

## State ownership and transitions
Supervisor stores run_id, task_id, lane_id, lease_epoch, browser_profile_binding, session_id, conversation_id, request_id, event_id, checkpoint SHA, checkpoint parent, and full transition timestamps. External workers provide authenticated evidence; the model's text is untrusted.

Per-lane phases:
`EMPTY -> READY -> EXHAUSTED -> CHECKPOINTED -> RESTORE_PENDING -> READY`.
Exception phases `FROZEN_UNKNOWN` (Send / delivery outcome not reconciled), `PAUSED_OWNER` (ownership or draft conflict). Stop is run-global and first fences future work; only positively owned, idle tabs may be closed.

Per-request Send states: `NONE -> INTENT -> SAVED -> RESPONSE_VERIFIED`. `INTENT` is written BEFORE clicking Send. Unknown confirmation at any later step freezes the lane and blocks automatic repeats, even after browser/MV3 crash. `RESPONSE_VERIFIED` is not `TELEGRAM_DELIVERED`.

`SESSION_EXHAUSTED` can rotate only when no Send is in INTENT/SAVED, no output transfer is in progress or unknown, and existing task state is stable. A checkpoint must carry source run/lane/epoch/session/conversation/task, outstanding artifacts, last verified turn, summary/body digest, permitted sources, and provenance. The new session is not READY until independent observed restoration matches the checkpoint digest and owned conversation.

## Continuous loop
1. Load a verified durable plan and current leases. The scheduler advances at most one transition per lane/tick and never fabricates browser evidence.
2. Respect per-lane FIFO, DAG prerequisites, user pause/stop, task-level retry budget for *non-Send* internal operations, and backpressure. Active lanes are bounded 1–5, never unlimited tabs.
3. To run indefinitely in *iteration count*, the backend creates new task IDs from an owner-approved recurring template, subject to task budgets, fair scheduling, tool permissions, provider usage limits and explicit stop criteria. Repeated tasks must not reuse external request_id.
4. A terminal or ambiguous account/usage limit is a pause, not an instruction to create more accounts or evade quota.
5. At a verified session exhaustion, drain nonterminal work, capture and persist a checkpoint, bind an owned new session, prove restoration, then continue the pending task once.
6. On MV3 suspension, Gateway/Relay restart or tunnel outage, reconcile durable event and Send ledgers before reopening work. Never infer success from HTTP 200 or browser tab existence.

## Evidence/authority
Events: (event_id, run_id, lane_id, lease_epoch, type, observed_at, request_id?, evidence_digest, actor, MAC). Server validates actor/epoch, deduplicates event_id with matching bytes and rejects conflicting replay. Persist event and transition atomically before ACK. Only trusted browser receipts may mark MESSAGE_SAVED / RESPONSE_VERIFIED. Telegram `bot_api_accepted_for_relay_response` with matching request_id is required for end-to-end delivery.

## Crash and scale boundaries
Snapshot JSON from browser storage is *untrusted* until signed reconciliation with backend and independently verified tab/conversation ownership. Event ledgers require durable append-only storage and bounded, verifiable compaction at scale; the prototype's in-memory JSON map is not a production store. Guard memory, disk, prompt budgets, context length, heartbeat and 1–5 lane fairness. No unbounded retries, backoff loops, provider calls or tab creation.

## Current implementation slice
`planning/continuous-execution-20261113/continuity_policy.mjs`: a synchronous pure reducer that rejects stale epochs, wrong conversation/lease evidence, duplicate request IDs, and unknown sends; suggests only non-Send actions. `test_continuity_policy.mjs` exercises 100 synthetic tasks plus negative transitions. It intentionally cannot operate ChatGPT or resume a real session.

## SDD increment — AutoPilot 0.8.5 session readiness and A2/A3 (2026-11-14)

**Authority/source:** existing AutoPilot 0.8.5 draft in
`autopilot_yandex_crx3_085_draft_20261114/Hermes-AutoPilot-0.8.5/`,
Web Companion 0.9.7, Relay's existing request/lease/noReplay ledger.
Implementation is **staged only**, not the loaded user extension.

- **FR-AP-001 (no empty tab = no readiness claim):** WHEN the supervisor
  creates an owned `https://chatgpt.com/` tab, THEN it SHALL report
  `NOT_PROBED/LOADING` until the top-frame, exact origin, documentId, single
  visible editable composer and mode-selection evidence pass. An empty home
  screen is not a dispatched or completed request.
- **FR-AP-002 (Chat mode):** WHEN the verified Chat/Work segmented control
  reports Work selected, THEN a bounded probe MAY click only the unique Chat
  control and SHALL require a *subsequent* READY observation; an ambiguous
  control, foreign origin, missing documentId or auth prompt fails closed.
- **FR-AP-003 (bounded navigation):** WHEN loading, DNS, browser service
  worker errors or offline states occur, THEN the monitor SHALL return typed
  outcomes with attempts/timeouts, not a false ready ACK. It SHALL NOT
  issue Send or create another tab as a substitute for a missing confirmation.
- **FR-AP-004 (reuse, backpressure):** WHEN `maxConcurrent=3`, THEN exactly
  three slots can be created, with two more only after owner changes capacity
  to 5. One slot SHALL serve sequential tasks; no extra tabs for retries.
- **FR-AP-005 (crash/recovery):** WHEN an idle, owned tab is unreadable for
  three checks, THEN at most two persisted refresh attempts with cooldown are
  allowed; assigned or pending/UNKNOWN-send tabs SHALL NOT be refreshed,
  rotated, or closed without reconciliation. OFFLINE does not trigger reload.
- **FR-AP-006 (A2/A3 contract):** `A2A_SESSION_V1` SHALL expose
  CAPABILITIES, STATUS, ENSURE_READY, PREVIEW_TURN, SEND_INTENT, CLOSE_IDLE
  with exact run/worker/task/epoch and owner binding. The `role` string is
  metadata, not authentication; current entry point is extension-internal.
  Missing verified Web Companion adapter SHALL return
  `TRUSTED_WEB_COMPANION_NOT_CONNECTED`. Browser DOM prefill is not Send.
- **FR-AP-007 (UNKNOWN-send fencing):** Before invoking a trusted Send adapter,
  the supervisor SHALL persist a per-turn `UNKNOWN_SEND` record with readback.
  Repeated attempts SHALL return reconciliation-required, never click Send.
  A Relay handoff ACK SHALL NOT mean model answer or Telegram delivery.
- **FR-AP-008 (stop/cleanup):** WHEN a run is stopped with unfinished tasks,
  THEN owned task tabs and checkpoints SHALL remain; a new run SHALL NOT
  silently accumulate more tabs. After owner closes preserved tabs, the old
  run SHALL be archived and read back before new tab creation. Idle tabs may
  be closed only with proven ownership and no pendingSend.
- **FR-AP-009 (observability):** A2/A3 and A5 receive redacted state
  code, tab ID, document evidence, attempt count, terminal/failure reason;
  never task prompt, session cookie, API key or full page text.
- **FR-AP-010 (provenance):** Only existing authenticated Web Companion
  receipt + Relay lease can establish a submitted message/verified reply.
  Fake local DOM answers, synthetic pages and mocked browser Port are not
  accepted as live E2E.

### Current implementation evidence / validation state

Source modules: `browser_ready.mjs`, `session_control_v1.mjs`, updated
`background.js` and existing `draft_dom.mjs` (unchanged; reused).
The candidate also auto-prepares at most two assigned drafts per heartbeat,
without Send and with the existing one-shot draft fence.

- Node readiness + A2/A3 controls: **25/25 PASS**,
  job `2f2ff35fbb6f49198d447d4f8fe8ecdb`, rc=0.
- Existing supervisor runtime lifecycle: **PASS (1/1)**,
  job `ac62ba4863f348c587ec0e7b4f4e156e`, rc=0; three then five
  tabs, no duplication, preserve incomplete tasks, archive stopped run.
- Real installed Yandex binary, disposable profile, **synthetic ChatGPT-origin
  page**: Work→Chat, visible input, offline/online, same-tab reload,
  synthetic assistant DOM and zero Send **PASS**, job
  `cb862e28310f4b5788fb373160444c3b`, rc=0. This does NOT
  verify real chatgpt.com, login, provider response or Telegram.
- Feature flag: SEND adapter **NOT CONNECTED**; actual end-to-end autonomous
  completion **NOT VERIFIED**. No user profile, Relay/Gateway or installed
  Web Companion updated by this change.
- GitHub isolation branch `feat/hermes-autopilot-stable-sessions-20261114`
  created from `Podtverzhdeno/Tunnel/main`. Local Hermes source is untracked
  in a separate parent Git repo; do NOT switch the dirty shared
  `IdeaProjects/main` branch.

### A2/A3 / A5 handoff and release gate

A2/A3 should bind an authenticated Web Companion adapter to
`session_control_v1.mjs` (not a second DOM send engine). A5 SHALL verify an
actual loaded extension ID and SHA in user-selected Yandex profile, one
real ChatGPT Web message → bound assistant response, Relay status, and
Telegram Bot API message-ID ACK; then inject offline/tab crash and 3-lane
reuse tests. Absence of proof is an open blocker, not PASS.
Additional A5 test plan/negative evidence is recorded in
`A5_HANDOFF_20261114.md`.

## Prerequisites for wiring to live Hermes
Verified current Web Companion receipt schema, chosen single AutoPilot variant/installed profile, durable backend event journal with CAS, current SHA/ownership checks, owner-approved feature flag, one real Send→Saved→Response→Telegram ACK E2E, crash/unknown tests, then 3→5 lane canaries. No change to live Relay/Gateway or 0.9.7 extension before all applicable gates.
