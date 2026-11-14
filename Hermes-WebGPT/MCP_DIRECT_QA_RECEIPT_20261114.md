# Hermes direct MCP/CDP acceptance receipt — 2026-11-14

## Objective
Telegram (existing Hermes Gateway) -> existing Relay's
`web-chatgpt-high-cdp` route -> persistent MCP stdio sidecar ->
Edge/Yandex CDP browser executor -> Relay request-bound result -> Hermes.

This is NOT a claim of live ChatGPT or Telegram end-to-end delivery.

## Defects closed in this run
1. **Unbound reply acceptance.** Previous worker considered any historic
   user turn sufficient when a new assistant response matched request_id.
   Red witness job `9fd995b28b874f02879f72f845c1e687`: both negative tests
   failed as expected (rc1). The worker now requires a *fresh* user turn
   containing the current Relay request UUID and exactly one new user turn
   before accepting a new assistant bubble. Absence/foreign message is
   `uncertain`; no Send retry.
2. **Contenteditable multiline round-trip.** Real Edge Playwright
   `locator.fill` expanded a blank-line separator into
   `<div><br></div><div>...</div>`. Neither `innerText` nor
   `textContent` was byte-equivalent to the prepared prompt. Added a
   structure-aware DOM text extractor and used it for pre-Send verification
   and untouched draft cleanup. Does NOT accept arbitrary text changes.
3. **Redacted worker error code.** Added code-only `failure_code` for
   expected `LiveCDPFault` cases; no prompt, browser URL, token or result
   is exposed in the worker receipt.

## Independently observed test results
- New worker unit and setup suites: job
  `9cb1c39df8a74ff0a26b29018bac03f7`, rc0,
  **23 tests PASS**.
- Existing durable Send journal: job
  `943c0a50d6a0486f9855dde5db6b90f8`, rc0,
  **22 tests PASS**.
- Real MCP SDK stdio live tool handshake and invalid-request denial:
  job `6dc4b5f07b0849cf9e311961edb7b7ff`, rc0;
  no browser Send.
- Real Microsoft Edge, Playwright, ephemeral real Relay HTTP/SQLite,
  synthetic ChatGPT-like HTML intercepted using Playwright route:
  job `5368d638d6ce4095b115ba7e88274396`, rc0.
  **4 scenarios PASS**: ready: 1 Send / Relay done;
  challenge: 0 Send / blocked; no new user: 1 Send / uncertain;
  foreign new user: 1 Send / uncertain. Zero real ChatGPT HTTP
  and Telegram messages; disposable browser profile.
- Installed Hermes CLI -> ephemeral Relay HTTP -> actual MCP SDK stdio
  sidecar/server -> real Edge `connect_over_cdp` -> Relay -> CLI:
  job `79e3dbafa4904b68bd08dd51dd481b55`, rc0.
  Single Relay request in `done`, exactly one browser Send,
  Hermes CLI exit=0, sidecar exit=0, expected synthetic answer
  received by CLI. **No real ChatGPT or Telegram traffic.**

- Native installed Hermes Telegram SDK inbound handler with a synthetic
  supergroup forum topic `thread_id=14` -> ephemeral Relay HTTP ->
  MCP SDK stdio sidecar -> isolated Edge CDP -> Relay ->
  Telegram SDK mock SendResult ACK in the **same chat and same topic**:
  job `b7e2f47fb6e840ba94f7762d3e64f264`, rc0.
  Single browser Send, one `done` Relay request, SDK inbound and mock
  outgoing ACK both true, returned metadata `{"thread_id":"14"}`.
  No real Telegram Bot API, account, chat or ChatGPT Web network request.
- Retested installed Hermes CLI model client after adding Telegram mode:
  job `e0fbd04ee6bc4d2ab2156fdb977f46cf`, rc0.
  Both modes use the same real MCP sidecar and same browser executor.

## Live environment readiness
Read-only `python -m hermes_mcp_cdp_preflight`:
job `1a0263c925d14f14bf228f29caacc6ee`, rc0.
Existing Relay reachable=true. Existing Playwright model route=false,
CDP browser reachable=false, worker principal present=false.
No live authorization, no user account touched, no Relay restart.

## Branch and scope
Draft PR: https://github.com/Podtverzhdeno/Tunnel/pull/8
Branch: `hermes/mcp-direct-web`, base `hermes/a4-installer`.
Publish only the selected, synthetic-test source and this receipt.
Do not merge as production ready.

## Remaining acceptance gates
- Owner-owned, isolated Yandex CDP profile started and manually signed
  into ChatGPT; selected unique `https://chatgpt.com/c/...` tab.
- Normal, owned maintenance restart of existing Relay with
  `--enable-playwright`; verify `web-chatgpt-high-cdp` and worker
  principal without replacing/stealing in-flight legacy extension jobs.
- One request-bound **real** ChatGPT Web response with one Send and
  matching Relay `saved` + `result` ACKs.
- One real Telegram incoming update in a known chat/topic, existing
  Hermes Gateway model route `web-chatgpt-high-cdp`, and independent
  Telegram Bot API outgoing sendMessage ACK in the *same topic*.
- Operational error and session-recovery checks after a real model
  response; no inference retries after any ambiguous Send.
