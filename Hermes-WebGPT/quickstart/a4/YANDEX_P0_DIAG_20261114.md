# A4/A1 integration receipt — Yandex AutoPilot empty tabs, 2026-11-14

## Verified on the user's development PC
- The current Yandex browser has an AutoPilot registration in its Default profile (a registration is **not** an enabled/delivering extension).
- Actual read-only A4 diagnostic returned:
  - \`status=NO_CONTENT_SCRIPT_HANDOFF\`
  - \`autopilot_registered=true\`
  - \`declared_content_scripts=false\` (no declarative content script in registered manifest metadata)
  - \`web_companion_bridge=AUTH_REQUIRED\` (Bridge localhost HTTP 403 with no key; expected authorization behavior)
  - \`prompt_dispatched=false\`, \`chatgpt_response_verified=false\`, \`telegram_delivery_verified=false\`.
- Local probe job: \`4ebdcbc4f56a4575817a71dbec41ca09\`, rc0.
- User-visible five blank ChatGPT tabs are consistent with the prior A1-confirmed diagnosis in \`Hermes-AutoPilot-Deploy-20261113/YANDEX_EMPTY_TABS_DIAGNOSIS_20261114.md\`: old 0.8.1 SAFE_DEMO opens tabs but does not dispatch prompts.
- Diagnostic only reads extension registration metadata. No tabs opened, no chat sent, no secrets output, no active run stopped.

## Implemented this cycle
- \`quickstart/a4/yandex_handoff_readiness.py\` — safe, read-only browser registration + localhost Bridge status classifier; output contains no extension IDs, profile paths, personal metadata or raw Bridge payload.
- \`quickstart/a4/test_yandex_handoff_readiness.py\` — synthetic Yandex fixtures: missing handoff, explicit SAFE_DEMO, registered non-demo without verified send, unrelated extension redaction, 403 distinction, missing profile. Focused Windows tests: 7 total, **6 PASS / 1 SKIP** (symlink permission), rc0, job \`3fc1f528b5c04976998339742781ef2a\`.
- DEV GUI button **«DEV: почему вкладки Яндекса не работают?»** shows useful diagnosis without blocking local Hermes startup. Real Windows WinForms parser/constructor: 4 PASS / 1 SKIP, job \`7334bd58447249b4a87e8e26ea49f01c\`. Github PR #6 has the smaller version of the same GUI enhancement (isolated from concurrent local A5 release GUI changes).
- Windows Quickstart full regression: **214 total, 205 PASS, 9 SKIP, 0 FAIL**, job \`192ab5440fc0413a935509bd79677113\`, rc0.

## Remaining functional P0 (A1 → Web Companion; not an A4 installer gate)
1. A1 builds actual non-demo AutoPilot content-to-Web Companion handoff, with a durable one-task lease/epoch ownership and an explicit one-time Send. Not another fake task overlay.
2. Check the user's active browser run and only replace an installed extension after permitted migration (do not reset active/unknown sends).
3. Verify one real ChatGPT message saved and a real response, then three concurrent independent streams, then image/file upload/download and Telegram delivery.
4. A4 consumes only an A1/A3/A5-approved build and registers its checksum, does not implement another transport queue.
5. **No E2E green status until actual delivery receipts exist.**

Development remains unblocked by A4 production permission checks. No production claim.
