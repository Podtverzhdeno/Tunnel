# Hermes A4 — Windows one-click + tool permissions (Draft)

Updated 2026-11-14. **Production: NOT READY.** The configuration wizard is a working code candidate, but does not yet launch a security-enforced live MCP environment. The existing 28-tool MCP/Telegram runtime on the user's PC must not be silently replaced.

## Local development mode (explicit owner opt-in)

**DEV startup is unblocked.** Double-click `Start-Hermes-DEV.cmd` or use the DEV button in the Windows wizard. The `-DevMode` switch bypasses only the A4 installer stop gate; it does not grant extra Windows rights or change existing MCP credentials/permissions. During development `Start-Hermes-All.cmd` defaults to `-DevMode`; use `Start-Hermes-STRICT.cmd` or set `HERMES_A4_STRICT=1` to restore the production gate. This does not alter OS, MCP or Tunnel grants.

Instructions and evidence: `quickstart/a4/DEV_MODE.md`. Keep **DEV readiness** separate from **production acceptance**: A5 sign-off and the scoped broker are not launch requirements for local development. GUI `read_only` is not an effective restriction on the current 28-tool MCP in DEV mode.

## Offline A4 source runtime update (2026-11-14)

A new local-only `v0.0.1-candidate` source archive contains all 24 required Python modules. Compared with previous `v0.0.0-candidate`, `relay.py` and `start_bridge.py` changed. The pinned archive was staged in an isolated temporary Windows directory, its sources imported via `relay.py --help`, and a separate real Relay HTTP server was started with a fresh SQLite database, an ephemeral loopback port and exactly three active slots. `/health` and authenticated `/bridge/status` succeeded; no jobs were sent, no live service switched. The private ZIP is not published or included in this PR.

Detailed checks, pinned SHA256 and job receipts: `quickstart/a4/RUNTIME_REFRESH_20261114.md`. **Not** a clean-clone installer or ChatGPT Web/TG E2E acceptance.

## Loaded Relay parity and guarded recovery

`quickstart/a4/loaded_runtime_parity.py` checks the four Python source SHA256
captured by the loaded Relay at startup against the current checkout; a green
health port alone is not evidence of fresh code. The read-only status is
exposed by the DEV diagnostics, WinForms GUI and `Start-Hermes-All.cmd`.

`quickstart/a4/relay_reload_preflight.py` checks exact process ownership,
terminal-only queue and Gateway idleness. `guarded_relay_reload.py --apply`
is an optional explicit one-shot guarded reload but was **not** applied in
this test cycle. UNKNOWN must never be retried without reconciliation.

Actual Windows mismatch, safe recovery of offline Relay/Gateway and evidence:
`quickstart/a4/RELAY_RUNTIME_PARITY_RECOVERY_20261114.md`.
This is not ChatGPT/Telegram E2E acceptance.

## Candidate source files

- `Setup-Hermes-A4.cmd` — double-click opens PowerShell 5.1 / WinForms UI for Windows 10/11.
- `quickstart/a4/setup.ps1` — mode (`read_only` / scoped `full_access`), folder, Yandex browser, 1–5 lanes, encrypted optional keys, saved settings, status diagnostic, previous-settings recovery.
- `quickstart/a4/preflight.py` — no side effects: validates settings and loopback MCP/Tunnel/Relay health. Always reports real tool enforcement as **unverified** until transport is integrated and tested.
- `quickstart/a4/policy.py` — deny-by-default allowlist for read-only and scoped file operations. Arbitrary commands, Git mutation and unsupported methods are denied.
- `quickstart/a4/mcp_guard.py` — isolated MCP stdio guard; `tools/list` only advertises approved tools and `tools/call` checks mode/workspace/path.
- `quickstart/a4/observer_profile.py` — prepares a minimal native read-only HTTP host config using only one selected workspace, no write/execute/git grants, and refuses to put the config inside the exposed workspace.
- `quickstart/a4/observer_runtime.py` — on an explicit button click, starts an isolated authenticated native HTTP `observer` on loopback port 18766; fails closed on occupied/unknown ports, reuses verified matching owned process, checks that exactly six read-only tools are advertised, records PID with no token. The GUI stores observer bearer using current-user DPAPI. **This does not change the existing MCP/Tunnel routing, nor does it claim remote ChatGPT connectivity.**
- `quickstart/a4/recovery.py` — allowlisted backup/restore of setup settings and encrypted DPAPI blobs. A previous version may be restored without destroying the current bytes, which are saved as `.rollback-current`. No automatic state migration.
- `quickstart/a4/vendor/hermes-install.ps1` — MIT-licensed official NousResearch Windows installer pinned to upstream commit `ae3707b97e50e65c2031cff6b84ce5fc81024d11` and SHA256 `31dd1373eae71908cac5326d4ffd8adc85d131ff509916df30b2836e4e231b0f`, accompanied by its upstream license. The GUI checks the script hash and asks for user permission before running `-NonInteractive -SkipBrowser -SkipComputerUse -Channel stable`. This installs **only the Hermes Agent core**; current stable dependency resolution remains controlled by upstream, and MCP/Tunnel/Relay/ChatGPT extension require further setup. No core install was executed during these tests.
- `quickstart/a4/tunnel_cli_probe.py` — read-only detection of the installed tunnel-client binary and supported CLI switches, without opening connections or reading tokens. Live Windows result: 0.0.19 supports `runtimes connect`, `--alias`, `--mcp-server-url` and `--runtime-api-key` (job `a2dcd781b4ef4d139d7c09ca48413cc4`). A new *dedicated* read-only Tunnel ID and owner-side approval are still required before routing; never silently repoint the working `local-workspace` connection.
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

Local Windows result 2026-11-14: 78 enumerated; **76 PASS, 2 SKIP** (Windows symlink privilege), exit code 0, job `b44fa29602cf4513b8dee0e17412cf49`. An earlier full run was blocked by a concurrent OneClick mutex (expected duplicate-start safety), but its isolated rerun and subsequent full regression passed. The fresh-install source inventory now explicitly reports missing Relay/Gateway/MCP files rather than claiming clean-clone readiness. The pinned upstream installer was hash-verified, its MIT license checked, and its PowerShell syntax parsed; **no network installation was executed**. Real Windows WinForms construction smoke (`-SmokeTest`) passed; isolated native HTTP observer host started on a temporary test port, advertised six safe tools, denied direct file write and was terminated by the test. The legacy launcher was also proven to fail closed (exit code 2) when a user has selected unverified A4 permissions, without launching a new runtime. The generated observer config was also exercised against the installed native MCP host, which correctly exposed only six read-only tools. Synthetic real stdio MCP E2E confirms a denied write never reaches the backend. A synthetic DPAPI encrypted-storage roundtrip passed without revealing a real key. Existing OneClick was checked in DryRun (no process restart).

## A4 acceptance backlog (handoff to A5)

| # | Requirement | Current status | Completion gate |
|---|---|---|---|
| 1 | Double-click installer | PARTIAL | Clean Windows clone + actual guarded launch |
| 2 | MCP/Tunnel/Relay/Hermes start | PARTIAL | Verified first-time provisioning and launch |
| 3 | GUI wizard | **DONE — Windows interactive GUI accepted** | Four WinForms UI scenarios PASS (save read-only/full+consent, 3→5 workers, restore, consent denial); see `GUI_WIZARD_ACCEPTANCE_20261114.md`. Live deployment remains under #1/#4/#5/#10 |
| 4 | Choice/change of permissions | PARTIAL | Host-enforced mode-switch with owner consent |
| 5 | Technical permission boundary | PARTIAL | Actual HTTP/stdio connector isolation + negative E2E |
| 6 | Paths/keys/browser/lanes | PARTIAL | Real settings→runtime wiring and 3-lane E2E |
| 7 | Safe secrets | PARTIAL | Live process injection and secret-log inspection |
| 8 | Readiness checks | PARTIAL | Full startup and Telegram delivery E2E |
| 9 | Update & recovery | PARTIAL — GUI subtask DONE | SHA-pinned GUI bundle, real WinForms activation/rollback, fail-closed pointer and fault recovery all accepted; full Hermes Agent/MCP/Tunnel/Relay/Gateway runtime upgrade and live release acceptance still missing (see `GUI_RELEASE_UPDATE_ACCEPTANCE_20261114.md`) |
| 10 | Real PC installation | NOT DONE | Independent Windows 10/11 + Yandex production E2E |

Next executable step: agree on a single authoritative MCP profile/port for mode enforcement, implement that routed host boundary, then run real permission-denial tests without disturbing active 28-tool sessions. At no stage should an agent self-approve elevated OS permissions.

## A4 continuation — 2026-11-14 14:00 UTC (supersedes earlier test count)

- Added `quickstart/a4/observer_tunnel.py` and `test_observer_tunnel.py`. A dedicated `hermes-a4-observer` alias requires explicit owner approval and a **different** Tunnel ID; a receipt prevents duplicate operations and ambiguous retries. The existing `local-workspace` tunnel is never reconfigured.
- Verified against official openai/tunnel-client source: `--runtime-api-key env:CONTROL_PLANE_API_KEY`, `MCP_EXTRA_HEADERS=Authorization: env:NAME`, and inherited child process environment are supported. The GUI now passes the observer bearer **in memory**, not via a newly written plaintext header file. DPAPI remains the at-rest secret store.
- After `runtimes connect`, the connector runs `runtimes status hermes-a4-observer --json` and requires exact boolean `process_running=true`, `healthy=true`, `ready=true`; otherwise the attempt is **UNKNOWN**, never automatically retried. This is a local-status check, NOT remote ChatGPT E2E.
- Observer startup can archive an old PID receipt **only** when Windows confirms the PID is absent. Tests cover false/unknown PID states and archive conflicts.
- Last full Windows Quickstart regression: **94 enumerated; 92 PASS, 2 SKIP**, rc=0, Local Workspace job `ab620be80b984926922314350519b7ea`. Two Windows symlink privilege tests remain skipped. No production services were interrupted.
- The final live click-through in the actual Yandex Browser, owner-granted full access, clean clone dependency provisioning, release updater, independent A5 QA and Telegram↔ChatGPT↔Telegram delivery are still unverified. Keep PR **Draft**.


## A4 runtime packaging candidate — 2026-11-14

- `runtime_bundle.py` audits exactly the static 24-file Relay/Gateway Python dependency closure and builds a deterministic, strictly allowlisted **local-only** ZIP. It checks syntax, file hashes, size limits and common embedded-secret patterns. A clean audit is *not* a substitute for private-data and licensing review.
- `runtime_stage.py` can stage an owner-pinned local ZIP into a separate per-user versioned folder and independently revalidate hashes after extraction. No service, process, Tunnel, browser, Telegram or permission mode is changed or started; no release is automatically activated. Tests cover concurrent locks, corrupted ZIPs, changed staged files, missing modules and approval gates.
- Local candidate archive was built in the parent projects directory, intentionally **not published to GitHub**. Its ZIP SHA256 is `1d5455e424c2fda423e58edfc3d2e12aa8a13ee823fe9512465266599a841796`, manifest SHA256 `02a690fc1e49e9de4b4afe25238e96e9b20f327ef2419e3d311babdd703a6fba`. It contains user-owned code and needs A5 independent source/privacy review.
- Latest complete Quickstart Windows regression: **150 tests total, 143 PASS, 7 SKIP, 0 FAIL**, return code 0, job `4e80ec665aeb44f2b4bd92fce7e4a7eb`.
- See `A5_HANDOFF_20261114_RUNTIME.md` for the independent QA plan. This still does not prove first-run clean clone or end-to-end Telegram / Yandex ChatGPT.

## A4 continuation — 2026-11-14 14:13 UTC

- Independent isolated **real Windows GUI click-path E2E** (`quickstart/a4/gui_winforms_acceptance.py`, kept in local QA workspace) passed all four cases: save read-only, confirm full-access selection, deny full access without persisting changes, and restore previous configuration, with two synthetic DPAPI secret files. Job `1a49deced8d84a4b9e348bf1ba292e24`, rc=0; production services and settings unchanged.
- Fixed UX issue: declining full access now returns normally without a second warning dialog.
- Added **offline versioned release staging**: `quickstart/a4/release_manager.py`, with manifest SHA256 pin, per-file SHA256, path allowlist, cross-process update lock, atomic version pointer, previous-version preservation and explicit rollback. This affects only A4 release assets under a per-user state folder. It does **not** install or replace the full Hermes runtime, does not auto-fetch updates, and does not restart services. A trusted checksum must be supplied by the publisher/owner through a separate trusted channel.
- 10 focused release tests: **9 PASS / 1 SKIP** (Windows symlink privilege), job `a1093d15177a40cd8e993190e3c29ffb`. Full Windows Quickstart regression: **105 total / 101 PASS / 4 SKIP**, exit 0, job `3effb090fa884ced80124e2fdedf40b9`. Separate real GUI E2E PASSED.
- Clean-clone distribution **still incomplete**: branch currently lacks `Hermes-WebGPT/gateway_runner.py` and `Hermes-WebGPT/start_bridge.py`; full one-click setup must fail closed rather than advertise readiness. No Cloud/Telegram E2E or full-access OS sandbox acceptance is claimed.

## Browser extension release gating — 2026-11-14

- `extension_contract.py` examines Yandex-compatible MV3 extension sources without installing them: required service worker/content scripts, allowlisted ChatGPT + loopback hosts, bounded permissions, no external update URL/connection or path traversal. A **hash match is not user approval**, and output never asserts the extension is installed or delivers ChatGPT messages.
- Actual `Hermes-AutoPilot-Deploy-20261113/autopilot-v0.8.5-candidate` was inspected locally (job `efa0a15e007e49ffa928893913d0a484`): valid MV3 basic manifest, but `demo_only=true` and `production_candidate=false`. A1 must provide a real post-demo verified build.
- Windows extension intake tests: **10 total, 9 PASS, 1 SKIP** (symlink privilege), job `7dcb39edd8474cfbb73f4f9e6518fa72`. Full Windows Quickstart tests after all changes: **170 total, 162 PASS, 8 SKIP**, exit 0, job `5297ede717074945836179ba5ae76da9`.
- Contract for A1 and A5: `BROWSER_RELEASE_GATE.md`. Live install and ChatGPT↔Telegram E2E still absent.
