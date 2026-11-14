# Hermes AutoPilot 0.8.5 — Session Runtime engineering checkpoint
Date: 2026-11-14
Project: `Hermes-WebGPT`
Review branch: `feat/hermes-autopilot-stable-sessions-20261114`
Work scope: existing 0.8.5 draft, not a new browser transport.

## Status
**Ready for A2/A3 integration and A5 review, NOT production.**
- Existing supervisor / scheduler retained.
- Tabs bounded to configured concurrent lanes. Auto-draft is bounded and never clicks Send.
- ChatGPT readiness verifies top frame, origin, unique composer and document ID, attempts one Work→Chat switch with post-confirmation.
- IDLE-only controlled reload with small cap/cooldown; OFFLINE/foreign/auth/unknown states do not generate extra tabs.
- STOP preserves tabs with unfinished tasks; START after STOP refuses to accumulate more while these owned tabs still exist. When the owner closes them, previous run snapshot is archived before a new start.
- Session Control API v1 for A2/A3 exposes schema+operations; without authenticated Web Companion submit adapter, SEND_INTENT returns `TRUSTED_WEB_COMPANION_NOT_CONNECTED`.
- No model/Telegram response claims from script echoes.

## Evidence / actual job receipts
1. `node --test test_browser_ready.mjs test_session_control_v1.mjs`: 25/25 PASS, returncode 0, job `2f2ff35fbb6f49198d447d4f8fe8ecdb`.
2. `node --test test_autopilot_background.mjs`: 1/1 PASS, returncode 0, job `ac62ba4863f348c587ec0e7b4f4e156e`.
3. Real installed Yandex binary / disposable profile / synthetic origin: `run_yandex_readiness.py` exit 0, job `cb862e28310f4b5788fb373160444c3b`. Origin, editor ready, Work→Chat, offline/online, reload: true; one tab, zero Send, zero external requests; answer is a synthetic fixture, NOT model-generated.
4. Real `https://chatgpt.com/` in disposable non-authenticated Yandex context: `run_probe_real_chatgpt_yandex.py` exit 0, job `7fbcebf8385643d693b8f42cadf3a53f`. **HTTP 403**, page DOM loaded, no composer, no Chat/Work selector, no model response. The diagnostic script succeeded in *measuring* the failure; this is NOT an E2E PASS.
5. Historical media tests were separate and do not establish real Web Companion MV3 registration or signed-in model response in this turn.

## Failure/authorization model
- Missing `documentId` => `DOCUMENT_NOT_PROVEN`.
- No DOM editor => `COMPOSER_NOT_READY` with bounded retries, not Send success.
- Work UI selected => MODE_SWITCH_REQUESTED, then re-check. Ambiguous controls block.
- OFFLINE => terminal observation without reload/replay.
- Unknown send/timeout => persistent `UNKNOWN_SEND`, never auto retry.
- Live Web Companion v0.9.7 is not connected to `session_control_v1.mjs`; no browser click/Enter fallback was added. A2/A3 roles are labels on a same-extension message interface, **not authenticated external actors**.
- Signed-in Yandex profile, installed extension ID/SHA, actual ChatGPT response, and Telegram ACK are open.

## Code fingerprints
`background.js`: `a77611cf7a1aab0f69303aabd7f95e08995f38f1dce2d3887f84677e85f24e4a`
`browser_ready.mjs`: `7ac2248569662a1b8a7bc2aba4b2dd493a186700f18c9b7bb998ca1aa9559af7`
`session_control_v1.mjs`: `26741cafcbf8d40770de51e313bcd07143eb17e3e2dab7db17eb2a6a2d855190`
`supervisor.mjs` (unchanged): `ebf36775e1efadb9ec17800fa4b0ce554b0a5ebb442dc97284596a45bcfb2745`
`draft_dom.mjs` (unchanged): `23af6501b6f0e180b891b882980e8cdecdbe6992213e6f6e8dadc1685cb4a47b`
`popup.js`: `bacf4b7406c94dbe7c9d91a06aa4600202cd03f57b042acab21ba42a2e886f45`
`popup.html`: `2c6c4770ae801c507c8fdd55a4fa72feca27560dfe51ffcf1a2acdff3d24933c`

## Run again (Windows, Local Workspace projects)
```powershell
cd C:\Users\user\IdeaProjects\Hermes-WebGPT\autopilot_yandex_crx3_085_draft_20261114\Hermes-AutoPilot-0.8.5
node --test test_browser_ready.mjs test_session_control_v1.mjs test_autopilot_background.mjs
python run_yandex_readiness.py
python run_probe_real_chatgpt_yandex.py
```
The two Python convenience runners expect `Hermes-WebGPT\.venv-cdp\Scripts\python.exe` for Playwright.

### Review hardening
- A successful Work→Chat mode switch increments persisted per-tab mode budget rather than being lost when READY is observed later.
- An unresolved saved tab ID blocks restart even if the browser-session ownership map is lost after suspension.
- Final focused suite **27/27 PASS**, job `546acd52727749cdbb2e7e96f126b472`, returncode 0. This does not change the LIVE HTTP 403 / absent signed-in E2E limitation.

### Latest hardening checkpoint

- **37/37 Node PASS**, job `63921660de7f4ebdb47a5e63b1ef222e`. Send intent now needs a fresh run/task/epoch check and two durable unknown fences. IDLE close/reload is disallowed while a worker-level uncertain-send marker exists.
- **Actual Yandex binary with synthetic page PASS**, job `f4cfe90eb1014e3185808c59ed858819`: Work→Chat, offline/online, reload, and refusal of an unverified `/c/` composer; `max_open_tabs=1`, `send_clicks=0`, `external_network_requests=0`.
- The real signed-in ChatGPT E2E, authentic Web Companion 0.9.7 send lease, model response and Telegram provider receipt remain **NOT VERIFIED**. Avoid reporting synthetic answer DOM or script exit 0 as real completion.
- Remaining A2/A3 P0: implement a trusted reconciliation operation for persistent UNKNOWN_SEND worker/turn markers and an authenticated adapter. Without either, release remains blocked.

## SDD / handoff
- OpenSpec / SDD: `openspec/changes/add-hermes-continuous-execution/design.md`, FR-AP-001–010.
- Implementation checklist: `openspec/changes/add-hermes-continuous-execution/tasks.md`.
- A5 verification: `openspec/changes/add-hermes-continuous-execution/A5_HANDOFF_20261114.md`.

**Next:** A2 Web Companion authenticated send adapter, A3 external authorization and response receipts, A5 real Yandex signed-in E2E and 3-lane fault injection. No release/feature flag until these gates pass.
