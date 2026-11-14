# Hermes A4 — Windows one-click + tool permissions (Draft)

Updated 2026-11-14. **Production: NOT READY.** The configuration wizard is a working code candidate, but does not yet launch a security-enforced live MCP environment. The existing 28-tool MCP/Telegram runtime on the user's PC must not be silently replaced.

## Candidate source files

- `Setup-Hermes-A4.cmd` — double-click opens PowerShell 5.1 / WinForms UI for Windows 10/11.
- `quickstart/a4/setup.ps1` — mode (`read_only` / scoped `full_access`), folder, Yandex browser, 1–5 lanes, encrypted optional keys, saved settings, status diagnostic, previous-settings recovery.
- `quickstart/a4/preflight.py` — no side effects: validates settings and loopback MCP/Tunnel/Relay health. Always reports real tool enforcement as **unverified** until transport is integrated and tested.
- `quickstart/a4/policy.py` — deny-by-default allowlist for read-only and scoped file operations. Arbitrary commands, Git mutation and unsupported methods are denied.
- `quickstart/a4/mcp_guard.py` — isolated MCP stdio guard; `tools/list` only advertises approved tools and `tools/call` checks mode/workspace/path.
- `quickstart/a4/recovery.py` — allowlisted backup/restore of setup settings and encrypted DPAPI blobs. A previous version may be restored without destroying the current bytes, which are saved as `.rollback-current`. No automatic state migration.
- `quickstart/start.ps1` + `quickstart/services.py` — existing preconfigured service launcher accepts encrypted GUI Telegram token via current-user DPAPI → process environment. Existing `profile/.env` token takes precedence. Original guarded start workflow remains otherwise unchanged.

## Safety and known limits

1. Setup GUI deliberately **does not call** `Start-Hermes-All.cmd` until the live MCP permission boundary is installed, verified and the unguarded alternate route excluded. This is not yet a complete one-click installation.
2. User-selected `full_access` only allows a small, scoped file write subset in the test guard, not arbitrary execution; deployment requires OS-enforced filesystem and process sandboxing. Client-side `settings-a4.json` is NOT an authorization grant.
3. Stdio guard is not connected to installed HTTP MCP Tunnel. Native Local Workspace host also supports an `observer` profile: a read-only HTTP profile must be enabled and verified via real control-plane/owner-approved routing before claiming read-only safety.
4. The GUI never prints keys. Entered keys are serialized via PowerShell `Export-Clixml` with Windows DPAPI under `%LOCALAPPDATA%\Hermes-OneClick`. The gateway launch reads only the encrypted Telegram token when no configured profile token exists; the tunnel uses the existing DPAPI filename.
5. The installer never bypasses browser account sign-in or extension installation approvals. A successful port check or synthetic MCP test is NOT Telegram↔Web E2E.
6. Symbolic-link path races and host/transport bypass remain release blockers; do not advertise strict OS sandboxing from the candidate policy alone.
7. No live Relay, Tunnel, Gateway or browser profiles were modified or restarted during development.

## Reproducible tests

```powershell
python -m unittest discover -s quickstart -p "test_*.py" -t . -v
python quickstart/inspect_dryrun_readonly.py
```

Local Windows result 2026-11-14: 59 enumerated; **57 PASS, 2 SKIP** (Windows symlink privilege), exit code 0. Job `a925f08ac46d4c18a6bb1b6e4d707247`. Synthetic real stdio MCP E2E confirms a denied write never reaches the backend. A synthetic DPAPI encrypted-storage roundtrip passed without revealing a real key. Existing OneClick was checked in DryRun (no process restart).

## A4 acceptance backlog (handoff to A5)

| # | Requirement | Current status | Completion gate |
|---|---|---|---|
| 1 | Double-click installer | PARTIAL | Clean Windows clone + actual guarded launch |
| 2 | MCP/Tunnel/Relay/Hermes start | PARTIAL | Verified first-time provisioning and launch |
| 3 | GUI wizard | PARTIAL | Interactive UI smoke in Yandex/Windows |
| 4 | Choice/change of permissions | PARTIAL | Host-enforced mode-switch with owner consent |
| 5 | Technical permission boundary | PARTIAL | Actual HTTP/stdio connector isolation + negative E2E |
| 6 | Paths/keys/browser/lanes | PARTIAL | Real settings→runtime wiring and 3-lane E2E |
| 7 | Safe secrets | PARTIAL | Live process injection and secret-log inspection |
| 8 | Readiness checks | PARTIAL | Full startup and Telegram delivery E2E |
| 9 | Update & recovery | PARTIAL | Atomic release rollback under failure/stress |
| 10 | Real PC installation | NOT DONE | Independent Windows 10/11 + Yandex production E2E |

Next executable step: agree on a single authoritative MCP profile/port for mode enforcement, implement that routed host boundary, then run real permission-denial tests without disturbing active 28-tool sessions. At no stage should an agent self-approve elevated OS permissions.
