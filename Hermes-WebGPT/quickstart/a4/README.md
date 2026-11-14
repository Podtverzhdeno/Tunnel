# Hermes A4 — installer and permission boundary (candidate, 2026-11-14)

Status: **NOT PRODUCTION-READY**. Do not advertise this as complete one-click installation.

## Local Windows artifacts

- Root: \`Setup-Hermes-A4.cmd\` — double-click opens PowerShell 5.1 WinForms setup wizard.
- \`quickstart/a4/setup.ps1\` — select read-only / scoped full mode, working folder, Yandex browser and 1–5 lanes; optionally store Telegram/Tunnel credentials encrypted using current-user Windows DPAPI; save nonsecret settings under \`%LOCALAPPDATA%\Hermes-OneClick\settings-a4.json\`.
- \`quickstart/a4/policy.py\` — fail-closed authorization matrix and canonical path/symlink boundary checks.
- \`quickstart/a4/mcp_guard.py\` — real MCP JSON-RPC stdio tool-call filter, including \`tools/list\` filtering and \`tools/call\` denial. Run only with a trusted backend and expose only this guarded endpoint to the client.
- The *existing* preconfigured one-click service starter remains \`Start-Hermes-All.cmd\`; the A4 GUI intentionally **does not call it** until actual runtime enforcement is configured.

## Security rules

1. **Read only**: no file writes, command execution, Git mutation or arbitrary tool names.
2. **Scoped full access**: selected file write tools permitted *inside the chosen directory*. Arbitrary shell, scripts, tests and Git push remain blocked because they can escape the OS scope. A future OS-level sandbox is required for broader access.
3. Invalid tools, workspaces, paths and symlink escapes fail closed. The full MCP host must not remain exposed via a second connector, port or alternate transport; configuration alone does not enforce this.
4. Tokens are not written to repository config or logged. Existing legacy \`profile/.env\` and \`start.ps1\` are not modified by this candidate. Saving DPAPI tokens does **not** connect the legacy gateway.
5. Microsoft/Chromium browser approval is never bypassed. A one-click GUI may open the extensions page, but the user must approve extension installation in the browser.

## Reproducible checks on Windows

\`\`\`powershell
python -m unittest discover -s quickstart/a4 -p "test_*.py" -t . -v
\`\`\`

2026-11-14: 21 tests enumerated; **20 passed, 1 skipped** — including two real guarded subprocess tests (Windows symlink privilege), rc=0, job \`2d9cce5a21bf40d09dc6f1bf83dc882e\`.

## Remaining acceptance blockers (handoff to A5)

- Wire the guarded MCP transport into the real Hermes connector and *remove all alternate unguarded routes*. Validate permissions through real \`tools/list\` and denied write calls, not only unit tests.
- Integrate first-run dependency verification/installation with pinned official hashes, tool binary and process ownership, then upgrade the GUI from safe configuration to guarded start.
- Integrate DPAPI secret retrieval into the real Telegram/Gateway startup, with secure permission checks and no plaintext secret logging.
- Test clean Windows 10 and Windows 11 VMs and actual installed Yandex Browser extension loading.
- Verify idempotent MCP + Tunnel + Relay + Gateway start, restart/recovery and successful Telegram ↔ ChatGPT E2E. No live process was restarted by this A4 candidate.
- Independently review the scoped policy for path-race attacks (symlink TOCTOU) and incomplete MCP protocol passthrough (notifications, pagination, transport mismatch).

**No production release decision may be based on these unit tests alone.**
