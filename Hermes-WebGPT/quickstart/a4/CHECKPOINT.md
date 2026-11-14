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
3. PARTIAL: WinForms wizard exists, real headless construction PASS; owner-assisted live click-path pending. When: interactive smoke.
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
