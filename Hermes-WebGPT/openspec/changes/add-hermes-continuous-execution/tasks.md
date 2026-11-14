# Continuous execution — staged work items

- [x] Inspect existing Hermes AutoPilot 0.8.1 / 0.10.0 previews, 0.9.7 Web Companion, and source-of-truth handoff spec.
- [x] Implement isolated pure JS state reducer with 1–5 lane ownership, epoch, event dedup, checkpoint gating and noReplay.
- [x] Execute 26/26 local Node tests; include 100 synthetic tasks and session rotations across five lanes (actual returncode 0).
- [x] Stage a separate SQLite WAL/HMAC event journal with revision CAS, persisted SEND_INTENT and restart/duplicate/tamper negative tests (15/15 Python PASS, rc=0). Not yet wired to authoritative Relay actor identity or browser.
- [ ] Choose a *single* AutoPilot implementation/profile and validate its current installed ID, SHA, and current Web Companion protocol before integration.
- [ ] Implement authenticated durable backend ledger and browser storage CAS, crash replay, event compaction, lease epoch fencing and source-channel authorization.
- [ ] Extend plan scheduler with approved recurrence templates and resource budgets; bind per-task external IDs and partial artifacts.
- [ ] Implement real checkpoint capture/restoration with independent browser ownership and conversation verification; support upload/download and draft conflict.
- [ ] Verify one live nonduplicated Send→saved→verified response→Telegram Bot API ACK, with a fresh synthetic request only.
- [ ] Test timeout, stale ACK, foreign profile, 3/5 lane parallelism, rate limit, pause/stop, browser crash and tunnel disconnection.
- [ ] Only then approve rollout as a dormant, reversible flag. Do not replace 0.9.7 or restart active Gateway merely to deploy.

## Increment 2026-11-14 — AutoPilot 0.8.5 (SDD FR-AP-001–010)

- [x] A1. Inspect the latest AutoPilot v0.8.5 draft, Web Companion 0.9.7 and
  existing SDD before changing source; avoid replacing the separate transport.
- [x] A1. Create an isolated GitHub branch
  `feat/hermes-autopilot-stable-sessions-20261114`; leave dirty parent main.
- [x] A1. Add top-frame readiness and Work→Chat mode switch with typed failures
  (FR-AP-001–003). Offline/browser unknown never equals Send success.
- [x] A1. Fix bounded tab creation 1–5, dynamically add only missing tabs and
  reuse existing worker binding (FR-AP-004).
- [x] A1. Add bounded idle-only reload, no refresh of working/pending-send tabs
  and noReplay send-intent fence in the A2/A3 integration contract (FR-AP-005–007).
- [x] A1. Preserve unfinished tabs on Stop; block a new run while previous
  owned tabs remain; archive stopped snapshot before replacement (FR-AP-008).
- [x] A1. Run 25 Node readiness/control tests, 1 full supervisor lifecycle test;
  actual Yandex binary synthetic page Work→Chat/offline/reload smoke (rc=0).
- [ ] A2. Bind Session Control v1 to a **real authenticated** Web Companion 0.9.7
  transport. Require signed request/lease, browser document owner and noReplay.
  Do not connect through model-generated Relay credentials.
- [ ] A3. Provide an authenticated external control gateway for A2/A3;
  current Chrome message entry point is same-extension-only, not an external API.
- [ ] A1/A2. Prove real ChatGPT site loads in a dedicated signed-in Yandex
  profile; actual composer, Chat/Work state and a single nonduplicate Send.
- [ ] A2/A3. Verify assistant reply attribution with Web Companion's saved
  user turn/assistant node receipt, not free-text substring matching.
- [ ] A5. Validate production E2E ChatGPT request→response→Telegram ACK,
  network interruption, tab crash, mode switch, 3 parallel sessions and
  persistence across worker restart. Current synthetic test is NOT E2E.
- [ ] A5. Review branch diff/test provenance, select canary/rollback and
  explicitly approve any installation or live feature flag.

### Additional engineering tasks
- [ ] AutoPilot-Web Companion compatibility matrix keyed by extension ID,
  manifest version, source SHA and owner documentId; refuse unknown versions.
- [ ] Read-only redacted trace export for every session transition and
  sender-side ACK (no prompts/cookies in logs).
- [ ] Fault-injection test for actual Yandex extension unload/reload, DNS
  loss, stale Work mode and timeout-with-unknown-send; enforce no replay.
- [ ] Owner-visible Russian statuses for LOADING / WORK_SWITCH /
  COMPOSER_READY / SEND_UNKNOWN / ANSWER_VERIFIED / TELEGRAM_ACK.
- [ ] 3-lane soak and 5-lane capacity/backpressure acceptance after one
  verified live canary, not before.

Evidence: `A5_HANDOFF_20261114.md` and
`Hermes-WebGPT/docs/AUTOPILOT_SESSION_RUNTIME_20261114.md`.

