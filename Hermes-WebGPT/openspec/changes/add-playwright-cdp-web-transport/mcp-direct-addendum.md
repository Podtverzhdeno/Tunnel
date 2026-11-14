# MCP-direct CDP addendum — 2026-11-14

## Decision
The intended direction of data flow is Telegram → existing Hermes Gateway →
existing OpenAI-compatible Relay (model alias `web-chatgpt-high-cdp`) →
an independently running MCP-controlled Playwright/CDP worker → browser →
Relay → Hermes Gateway → Telegram.

**MCP cannot push a turn into the ChatGPT Web product by itself.** In OpenAI's
tool model ChatGPT is an MCP client, not a general-purpose inbound MCP
conversation server. Without a browser bridge or an independent ChatGPT
agent/automation that pulls work from MCP, a Telegram message cannot
spontaneously start a ChatGPT Web turn.

Our prototype uses the *already existing* `relay.py` `playwright` route.
It does not add a second Telegram token, bot update poller, Relay queue or
extension transport. The `hermes_web_cdp_mcp.py` stdio MCP server exposes
`hermes_web_transport_status` and `hermes_web_process_fixture_once`.
The latter wraps the audited one-shot `fixture_worker.execute_one` (pre-click
journal, per-request page ownership, one send, saved/result ACK or UNCERTAIN).
An external worker/sidecar must handle the queue while Hermes waits for the
model call; the model cannot call its own MCP tool while it is blocked waiting
for its Relay completion.

## Browser contract
- Playwright/CDP is supported for Chromium/Edge by the installed project-local
  Playwright 1.64.1 venv. No browser extension is needed.
- This implementation is **synthetic fixture only**; it does not automate
  chatgpt.com, reuse the signed-in Yandex profile, use private website APIs, or
  extract ChatGPT output.
- Real unattended output extraction is not enabled because the current
  OpenAI Terms of Use prohibit automatic/programmatic extraction of Output.
  An authorized official Responses API integration is the supported route
  for a fully unattended Telegram model backend; it is not represented as
  a ChatGPT Web session.
- MCP stdio is newline-delimited JSON-RPC, implements initialize, ping,
  tools/list, tools/call, and notifications/initialized; credentials are read
  from the fixture-only environment rather than tool inputs.

## Acceptance receipts
- MCP contract: `python -m unittest test_hermes_web_cdp_mcp -v`,
  Local Workspace job `6ffca51560ef4e85ae38da9f500898c1`;
  **8/8 PASS**, rc=0, includes actual child-process stdio protocol framing.
- Real Edge browser, Playwright, existing `relay.Server` on ephemeral local port,
  temporary SQLite, and real MCP `tools/call`: job
  `251072817d1748b8b64d278f9242218e`, **PASS**, rc=0:
  * ready: 1 Send, HTTP 200, Relay done;
  * challenge: 0 Send, Relay blocked, HTTP 502;
  * close_after_click: 1 Send, Relay uncertain, HTTP 502, no retry.
- Installed Hermes runtime Python lacks optional `mcp` client SDK,
  plus `pywintypes` for MCP imports in that environment. No live venv touched.
- Project-local `.venv-cdp` now has the Hermes-declared optional `mcp>=1.2,<2`
  SDK, resolved version **1.30.0**; install receipt job
  `4361111c73b948c9a96ef54b3611b18b`, rc=0.
- Real `mcp.ClientSession` stdio handshake: job
  `15115051728a4729aac8c66601eb9d28`, rc=0,
  discovered both MCP tools and invoked status.
- Actual installed Hermes CLI through Relay → true MCP SDK stdio call →
  real Playwright Edge fixture → Relay → same Hermes CLI:
  job `92a70d66a4df4a388950be94db118249`, rc=0,
  CLI received `LOCAL_FIXTURE_OK`, one synthetic Send.
- Actual installed Hermes MCP registry from the isolated project test venv:
  job `76342bbe7ded4b21bb38759f1a979945`, rc=0.
  Both tools registered under the `mcp_hermes_web_cdp_fixture_` prefix.
  No running Gateway or real profile modification.
- Latest transport/relay focused regression: job
  `16ed667fba134295bf208ee7488003a1`, rc=0, **47/47 PASS**.
- Existing live Hermes/Telegram browser workers, their SQLite, keys and
  profile were not touched. No live Telegram delivery E2E is claimed.

## Outstanding
1. Stage the Hermes MCP SDK dependency in an isolated runtime and run the
   actual `mcp.ClientSession` discovery (do not retrofit running profile).
2. Integrate an independently running worker into a test Gateway via the
   existing CDP model alias; verify source update → model completion → exact
   Telegram chat/topic SendResult.
3. If a ChatGPT Web MCP-native route is desired, create a pull-based
   ChatGPT-side inbox/outbox plugin and a supported agent trigger; MCP alone
   cannot asynchronously invoke ChatGPT.
4. Do not enable autonomous scraping of ChatGPT Web Output or bypass browser
   challenges. Use an official model API for unattended live production.
