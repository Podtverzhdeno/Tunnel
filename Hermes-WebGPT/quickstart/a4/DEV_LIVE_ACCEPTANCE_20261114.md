# Hermes A4 — DEV live acceptance, 14 November 2026

## Result: local DEV service startup idempotence PASS

Windows device used via the connected Local Workspace `projects/Hermes-WebGPT`.

1. Pre-launch OneClick dry-run: `MCP=True, Tunnel=True, Relay=True`, Gateway `already_running`. Job `6704860eb62546fbb80b81b0af7a2c49`, rc0. No changes.
2. Separate read-only deep diagnostic: MCP 28 tools, Tunnel /readyz=ready, Tunnel control-plane `state=polling`, Relay /health 200 and valid protocol; Gateway process identity attested. Job `7c368674c600401bb85a05591fdf9ccf`, rc0.
3. DEV preflight (no launch): four required processes/services attested, job `48d3ede54f2e47468f71d39f753ebacc`, rc0.
4. Actual DEV startup invoked by `quickstart/a4/verify_dev_running.py` only after four ready checks. Job `ea95e305f32e465291fd93e09d60ef72`, rc0, **PASS**. `start.ps1 -DevMode` returned rc0; Relay and Gateway identities/receipts unchanged. Messages sent by test=0, uploaded files=0, browser interactions=0. No duplicate launch observed.
5. After-run read-only diagnostic remains locally ready, job `8193ccb3599d4b19aea4158fc3edcb96`, rc0.
6. Added DEV readiness module `quickstart/a4/dev_status.py` (6/6 unit tests PASS); idempotency tester `verify_dev_running.py` (5/5 PASS), WinForms status button (headless parser/constructor PASS).
7. Full Windows Quickstart regression after A4 changes and concurrent token-migration fix: **202 tests; 194 PASS, 8 SKIP, 0 FAIL**, rc0, job `d0e2942ffbaf461394156009333a0b52`.

## What this proves

- Preconfigured DEV Hermes can be invoked safely without triggering A4's production-only permission blocker.
- Existing MCP, Tunnel, Relay and Gateway stay up; no duplicate or unintended restart detected.
- Runtime can be inspected from the GUI using `DEV: проверить работающие службы` without cloud operations.

## What this does not prove

- A `/readyz` response and control-plane `polling` are not a verified cloud ChatGPT tool call.
- No actual Telegram ↔ ChatGPT ↔ Telegram message or file/image E2E was sent.
- No real Yandex extension install, empty-tab regression, multi-lane delivery or clean Windows clone acceptance occurred.
- DEV mode intentionally does not enforce GUI-selected read-only rights on the existing 28-tool MCP; user-defined grants and OS permissions remain unchanged.
- The service check is not proof of background continuous operation or auto-restart after computer reboot.

## Near-term development tasks (not production blockers for DEV)

- A1: verified non-demo AutoPilot/Web Companion in Yandex; fix blank automatically created tabs.
- A2/A3: real 3-stream sessions and text+media E2E with delivery receipts.
- A4: complete clean-clone dependency provisioning, first-run wizard, rollback and owner-reviewed install.
- A5: independent Windows 10/11 acceptance and scoped permissions release review.

**Status:** DEV local startup works and has a real idempotent startup receipt. Production setup still partial. Keep Draft PR #6.
