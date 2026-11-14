# A4 checkpoint — 2026-11-14 13:03 UTC

## Source of truth
- Local Windows checkout: Local Workspace \`projects/Hermes-WebGPT\`, A4-owned files under \`quickstart/a4\`.
- GitHub: \`Podtverzhdeno/Tunnel\`, branch \`hermes/a4-installer\`, Draft PR #6. Do not merge until A5 accepts it.
- Shared checkout is rooted in an unrelated large Git tree; NEVER stage/commit its other files.
- Hourly \`Hermes A4 — до приёмки\` task exists; do not duplicate.

## Verified
- Full Windows Quickstart tests: **78 total; 76 PASS / 2 SKIP**, rc=0, job \`b44fa29602cf4513b8dee0e17412cf49\`. Two skips are Windows symlink privileges, not passes.
- One earlier run failed due to transient active \`HermesOneClickStartup\` mutex. Isolated dry-run reran PASS (job \`2d09d62349174c63bf97f506f476a316\`) and later full run passed. Preserve duplicate-start protection.
- The native \`observer\` profile passed 15/15 host tests on Windows in prior run.
- Actual isolated native MCP HTTP host started on an ephemeral test port, advertised exactly **six** read-only tools and refused a forbidden \`write_file\`; the test stopped its own process. This is NOT proof of live ChatGPT cloud/Tunnel routing.
- WinForms GUI construction and PowerShell syntax parsed without showing windows or changing real profile.
- Windows DPAPI synthetic-key roundtrip passed. No real credentials printed.
- Official MIT-licensed Hermes Agent installer pinned at upstream Git commit \`ae3707b97e50e65c2031cff6b84ce5fc81024d11\`, SHA256 \`31dd1373eae71908cac5326d4ffd8adc85d131ff509916df30b2836e4e231b0f\`. Bundled and hash checked; **not executed**, awaiting GUI user consent.
- Read-only tunnel CLI probe (job \`a2dcd781b4ef4d139d7c09ca48413cc4\`) identified installed \`tunnel-client\` 0.0.19 supporting \`runtimes connect --alias --mcp-server-url --runtime-api-key\`. No connection was created or modified.

## A4 requirements: strict acceptance
1. PARTIAL: one-click GUI/launcher exists; clean clone boot not accepted. When: verified initial dependency provisioning + Windows install.
2. PARTIAL: existing MCP/Tunnel/Relay/Gateway launcher can restart preconfigured services; new isolated observer host can start. When: all four auto-provisioned under enforced rights.
3. DONE (GUI scope): actual Windows WinForms interaction accepted in four isolated scenarios: read-only save, approved full-access save with 3→5 workers, restore, denied full-access no-persist. DPAPI synthetic secrets verified. Evidence `GUI_WIZARD_ACCEPTANCE_20261114.md`; independent opt-in real UI tests rc0 twice. This does NOT authorize production MCP permissions or close #4/#5/#10.
4. PARTIAL: read-only/full-scoped selection saved; full trusted host broker not wired. When: owner-granted mode change independently verified.
5. PARTIAL: native read-only host + scoped stdio filter tested; full OS sandbox and actual cloud-route isolation pending. When: direct live denied-write + bypass audit.
6. PARTIAL: paths/browser/workers/optional keys fields exist, core boot only; worker count not bound to actual orchestration. When: live worker orchestration verified.
7. PARTIAL: user DPAPI storage and gateway token fallback tested; live log/ACL review pending. When: actual process owner/user acceptance.
8. PARTIAL: health checks and fresh-clone source inventory. When: full system + Telegram delivery E2E.
9. PARTIAL: rollback of settings/DPAPI blobs; release updater not implemented. When: tested atomic component update, rollback.
10. NOT DONE: clean Win10/Win11 user-PC E2E including Yandex extension approval, Telegram delivery. When: after previous gates plus independent A5 tests.

## Current blockers / next safe work
1. **Primary next step:** prepare a dedicated \`hermes-a4-observer\` Tunnel route without changing existing \`local-workspace\` alias. The user must supply/authorize a distinct Tunnel ID and the real connector registration. Use installed CLI 0.0.19 only after verifying header handling. Do NOT log bearer/API keys and DO NOT reuse the production Tunnel ID without consent.
2. Complete release dependency provenance for MCP/Tunnel/Relay/Gateway from clean public checkout; the pinned installer only provides Hermes Agent core.
3. Build owner-visible GUI status for live observer and installer, robust stale receipt recovery after reboot, stop/restart and no-downtime policy switch.
4. Implement trusted OS-enforced scoped full access; do not expose legacy 28-tool host over HTTP.
5. Obtain A5 independent review and real E2E on Yandex/Telegram before a final approval.

## Commands
\`\`\`powershell
cd <your-checkout>\Hermes-WebGPT
python -m unittest discover -s quickstart -p "test_*.py" -t . -v
python quickstart\a4\tunnel_cli_probe.py
python quickstart\inspect_dryrun_readonly.py
\`\`\`

Guardrails: never run live installer, change MCP/Tunnel/cloud routes, overwrite user browser profiles, or export plaintext credentials without explicit owner action. Reports must enumerate statuses for items 1–10 and specify the next completion gate, not invent calendar ETAs.

## Updated A4 checkpoint — 2026-11-14 14:00 UTC (current)

- New local files: `quickstart/a4/observer_tunnel.py`, `test_observer_tunnel.py`, `inspect_connect_contract.py`. The latter is a CLI help inspection utility, no credentials.
- New tests require managed local Tunnel status: exact booleans for `process_running`, `healthy`, and `ready` before marking local success. Remote ChatGPT connection is explicitly not verified.
- Dedicated tunnel bearer now travels via `MCP_EXTRA_HEADERS=Authorization: env:HERMES_A4_OBSERVER_AUTHORIZATION` in the child process environment; no new plaintext authorization file is created. Official openai/tunnel-client documents this syntax and passes parent environment through to managed child.
- Native observer reboot recovery archives dead PID receipts, never touches live or unknown process PIDs. Tested separately.
- Windows full Quickstart tests: **94 total, 92 PASS / 2 SKIP**, exit code 0; job `ab620be80b984926922314350519b7ea`.
- **Next:** selectively publish A4-only local changes to Draft PR #6 after SHA-based review. Do not publish/test helper files from other agents. Do not merge until independent A5.
- Hard blockers: user-granted dedicated Tunnel ID and connector app authorization; OS-enforced scoped full access; clean-install provisioning and restart E2E on Win10/11/Yandex; verified Telegram↔ChatGPT output; safe application update/rollback and A5 signoff.
- Report 1–10 statuses as in the matrix above. Earlier test counts in this checkpoint are historical only.

## A4 packaging continuation — 2026-11-14 (latest)

- New A4-owned files: `runtime_bundle.py`, `test_runtime_bundle.py`, `runtime_stage.py`, `test_runtime_stage.py`, `A5_HANDOFF_20261114_RUNTIME.md`. They are **local-only source packager + staged runtime verifier**, not a complete installer or remote deployment.
- Source ZIP candidate built *locally* at `projects/.hermes-a4-runtime-v0.0.0-candidate.zip`. Verified SHA256 `1d5455e424c2fda423e58edfc3d2e12aa8a13ee823fe9512465266599a841796`, manifest `02a690fc1e49e9de4b4afe25238e96e9b20f327ef2419e3d311babdd703a6fba`. It **must not be published** without independent source/privacy/license audit.
- Tests: `test_runtime_bundle.py` 10 PASS/1 SKIP; `test_runtime_stage.py` 9 PASS/1 SKIP. Full Windows Quickstart **150 total, 143 PASS, 7 SKIP, 0 FAIL**, rc0, job `4e80ec665aeb44f2b4bd92fce7e4a7eb`.
- Source closure contract `dependency_closure.py` still 24 Python modules for Relay/Gateway. Gateway/bridge modules are missing in the GitHub clean clone. No build was deployed to running services.
- Next admissible steps: A5 independent source/privacy verification; publish only approved module content; integrate release-candidate staging into one-click provisioning, include proven MCP/Tunnel/Relay/Browser dependencies, verify real read-only/full-scoped broker and Telegram E2E on clean Win10/11.
- A4 ten-point statuses: #3 GUI scope independently E2E tested; #1,#2,#4,#5,#6,#7,#8,#9 remain partially complete; #10 not complete. Full OS-enforced write/execute broker and owner-authorized cloud tunnel still block production signoff. **Do not merge PR #6.**

## A4 refreshed checkpoint — 2026-11-14 14:13 UTC (historical)

- PR #6 remains Draft; publish own A4-only changes (GUI refusal UX, offline updater) with SHA/CAS, do not include A5-owned GUI QA script without its owner.
- Tested: `gui_winforms_acceptance.py` rc=0 (job `1a49deced8d84a4b9e348bf1ba292e24`), all 4 real WinForms click paths passed in isolated temp AppData; user's real settings/keys untouched.
- Tested: full Quickstart **105 enumerated, 101 passed, 4 skipped**, rc=0 job `3effb090fa884ced80124e2fdedf40b9`. A4-only release manager tests 9 PASS/1 SKIP. Any earlier count above is historical.
- New local files: `quickstart/a4/release_manager.py`, `test_release_manager.py`. Offline A4 release version staging and rollback only; not a complete in-place updater.
- A4 1–10: 1 PARTIAL (clean clone missing components), 2 PARTIAL (no secured unified startup), 3 PARTIAL (GUI real click E2E passed, owner production acceptance pending), 4 PARTIAL (choice works, full broker unconnected), 5 PARTIAL (observer 6-tool read-only verified, full sandbox/remote isolation missing), 6 PARTIAL (GUI config not wired to orchestration workers), 7 PARTIAL (DPAPI synthetic tests pass, live secrets/log review missing), 8 PARTIAL (health checks, no Telegram E2E), 9 PARTIAL (versioned offline A4 update and rollback, not whole runtime), 10 NOT DONE (clean Win10/11 Yandex Telegram E2E + A5).
- NEXT: implement a safe, complete source/dependency distribution and user-approved dedicated observer Tunnel routing; **do not** change production tunnel 8765, push owner-specific keys to Git, silently grant full access, or merge before independent A5.
