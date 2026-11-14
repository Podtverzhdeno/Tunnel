# Hermes → ChatGPT Web directly through MCP / CDP (no extension)

**Status (2026-11-14):** implementation and simulated Relay acceptance built;
real authenticated ChatGPT Web ↔ Telegram delivery is NOT yet verified.

## Architecture

`Telegram → existing Hermes Gateway → existing Relay (model web-chatgpt-high-cdp)
 → Playwright route → MCP/CDP worker → dedicated Yandex ChatGPT Web tab
 → existing Relay ACK/result → Hermes Gateway → same Telegram topic`.

This is an **MCP-exposed browser worker**, not an inbound ChatGPT MCP
conversation API. OpenAI's MCP server direction is ChatGPT → external
tools. We do **not** call private ChatGPT endpoints or impersonate the API.

The old Web Companion route and its queue are left unchanged. Relay keeps
per-request transport identity; a request cannot be silently rerouted between
extension and Playwright. No second Telegram bot or parallel Relay exists.

## One-time configuration (development)

1. Install Playwright in project-isolated `.venv-cdp` (already present on the
   development machine) and ensure the Yandex Chromium binary exists.
2. For a **future, clean Relay launch**, set `HERMES_RELAY_CDP_ENABLED=1`
   and start the usual `start_bridge.py`. This adds `--enable-playwright`
   to the EXISTING Relay. It does **not** restart a running Relay; if Relay
   is already active without this route, reconcile its PID/state and perform
   a normal maintenance-window restart rather than launching a second one.
3. Run `powershell -NoProfile -File Start-Hermes-CDP-Browser.ps1`. This opens
   a new, isolated Yandex profile, CDP loopback port 9223, with extensions
   disabled. Manually log in to ChatGPT and open exactly one existing
   `https://chatgpt.com/c/<conversation-id>` conversation. **No cookie
   copying or login automation.**
4. In the existing Hermes inference config, use the Relay's custom base URL
   `http://127.0.0.1:18765/v1` and the `web-chatgpt-high-cdp` model.
   The existing Relay client key is used as before. Do not expose it in logs.
5. With the Relay's generated `data/playwright-worker-auth.json` present,
   start the separate executor (NOT a tool call from the model that is waiting
   for its own response):

   `powershell -NoProfile -File Start-Hermes-MCP-Web.ps1
      -ChatUrl https://chatgpt.com/c/<conversation-id>`

   This confirms the existing Relay model is CDP-enabled and the
   browser debugging endpoint is ready. It then starts the dedicated
   `hermes_cdp_mcp_sidecar.py` MCP client, which calls the EXISTING
   `hermes_web_cdp_mcp.py` stdio server's
   `hermes_web_process_live_once` tool repeatedly. That tool uses the
   project-isolated Playwright/browser executor and existing Relay queue.
   Worker bearer stays in process environment, never CLI arguments.
   Only `transport=playwright` jobs are claimed.

## Optional MCP tool surface

Start `hermes_web_cdp_mcp.py` as an MCP stdio server with
`HERMES_CDP_LIVE_ENABLE=1` and `HERMES_CDP_LIVE_WORKER_KEY` provisioned
from the existing Relay worker principal. Tool
`hermes_web_process_live_once` takes `relay_url`, `cdp_url`,
`chat_url`, `journal_dir`, and optional `timeout_seconds` (30–900).
The default fixture-only tool list remains unchanged without opt-in.

For a Telegram inference loop, use the separate long-running executor
rather than ask the waiting Hermes LLM to invoke its own worker.

## Result and retry contract

- A queued request is claimed with its Relay lease and exact request ID.
- CDP must attach to exactly one already-open, already signed-in ChatGPT tab.
- Unique editable composer, unique Send in same form, no auth challenge.
- Relay `intent` ACK plus durable `send_armed` journal **before** one click.
- User bubble and matching JSON assistant `request_id` are observed, then
  Relay `saved` and `result` ACK complete the request.
- A missing response, page closure or lost ACK yields `uncertain`; **no
  automatic resend or switching back to extension**.
- Returning Relay `done` is not proof that Telegram actually delivered a
  message; require a Bot API sendMessage acknowledgment in Gateway E2E.

## Known remaining gates

- CDP must be manually attached to an authenticated user-owned profile.
- The live ChatGPT DOM can change; no live conversation roundtrip has yet
  passed independent acceptance.
- Web UI streaming/markdown format may prevent exact JSON matching; fail
  closed rather than produce an invented assistant reply.
- Browser profile permissions, reboot lifecycle, session recovery, media
  upload/download and five independent sessions require separate acceptance.
- Do not call the feature production ready until a single request-bound
  Telegram → Hermes → ChatGPT Web → Telegram E2E canary succeeds without
  duplicate Send and with real Telegram delivery receipts.
