# Hermes A4 — temporary DEV-by-default decision (2026-11-14)

Owner-approved development shortcut: temporarily **remove the A4 production security-enforcement stop gate from the default local startup path** so development/testing can continue.

## Implemented

- `Hermes-WebGPT/Start-Hermes-All.cmd` now launches `quickstart/start.ps1 -DevMode` by default.
- `Hermes-WebGPT/Start-Hermes-DEV.cmd` remains an explicit equivalent.
- `Hermes-WebGPT/Start-Hermes-STRICT.cmd` launches `start.ps1` without the DEV flag, preserving the A4 pending-enforcement check. Alternatively set `HERMES_A4_STRICT=1` and double-click `Start-Hermes-All.cmd`.
- No global OS settings, firewall rules, MCP tool registry, system permissions, current tunnel credentials or user profiles were changed.
- DEV means the selected GUI permission model is **not enforced** on the existing 28-tool MCP. Its actual tool access remains what the currently running MCP grants. This is a local development convenience, not a production security guarantee.

## Windows evidence

- Focused developer/secret integration suite **11/11 PASS** (job `6202b767a0d44b7f89ae1410b07e8842`).
- Whole Quickstart Windows suite **203 total, 195 PASS / 8 SKIP / 0 FAIL** (job `8b383fe25fea4d8889e56aa36c1f49a4`).
- Live double-click-equivalent batch execution of the **actual** `Start-Hermes-All.cmd`: `PASS`, exit code 0, all four services pre-attested, Relay/Gateway identities and receipts unchanged, no messages or files sent (job `020387ab75a44792846442958166a5f2`).
- This was run against the already-configured development Windows machine; clean-install, browser/tab automation, 3 concurrent sessions and Telegram ↔ ChatGPT text/media E2E **remain unverified**.

## Development vs. release

- **DEV local launch: ready and verified.**
- **Production A4 release: still incomplete**; A5 approval, permission enforcement, clean clone, and browser/Telegram E2E remain separate release gates.
- Do not mistake permissive DEV startup for permission enforcement or real cloud availability.

## Next work

Focus on fixing Yandex AutoPilot blank tabs, building a complete installable runtime and demonstrating actual Telegram ↔ Web ChatGPT traffic. Production hardening need not delay those experiments.
