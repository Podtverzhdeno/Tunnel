# A4 GUI release candidate and native control QA — 2026-11-14

## Verified candidate (offline, NOT activated)
- Source: `projects/Hermes-WebGPT`.
- Bundle directory: `planning/a4-gui-release-v0.9.1-guiqa`.
- Builder receipt job `53332f64266d4491be35565e6ad6fce0`,
  rc=0, `files=3`, `activated=false`.
- Exact package contents: `Setup-Hermes-A4.cmd`,
  `quickstart/a4/release_launch.ps1`, `quickstart/a4/setup.ps1`,
  plus `manifest.json`.
- Pinned manifest SHA256:
  `b9fd1c27427f037d9e1826fb85b9128a9c039895d47e163dca44e62b0e9c5a34`.
- Verification: job `7dc1d00237904e9f88074b7e0cc492c4`,
  rc=0, `preview_only`, three files hash-verified.
- No user credentials, profile, browser session, Telegram message, live
  service restart, release pointer activation or production installation.

## GUI control regression and fix
- RED native WinForms run job `ed1167dcc4084db7bc1636ac86fd90b1`,
  rc=1: index-based `buttons[-3:]` started selecting the wrong actions
  after an unrelated DEV button was appended.
- Fix in `quickstart/a4/gui_update_acceptance.py`: select unique WinForms
  release action buttons by visible names, with explicit failure on ambiguity.
- GREEN actual WinForms interaction:
  job `737a189e96d548ecb6f2932624358208`, rc=0,
  one true native update+rollback case; repeated after other source edits
  in job `08ea14cd709540a49865782831778722`, rc=0.
- Headless selector contract: job `450fc63f8c4f47a297d511c8f248db05`,
  rc=0, 2/2 PASS.
- GUI bundle fixed so its CMD target `release_launch.ps1` is actually
  included; builder tests job `9b4ec85ba3d04dfa8c21319877d8c8dd`,
  rc=0, 3/3 PASS.

## Current full regression
- Job `b5b6f5aa03ca46158af5f7886b0c82dd`: rc=0,
  202 tests, 194 PASS / 8 SKIP / 0 FAIL.
- New isolated migration tests also reverified after concurrent patch:
  job `4df3a15aba0e4f588aeb97c0e364de8c`, rc=0, 3/3 PASS.
- Skipped tests are not production E2E proof.

## GitHub state / remaining blockers
- Draft PR #6 on `hermes/a4-installer` has the tested builder and selector
  implementation and tests, but the PR's `quickstart/a4/setup.ps1`
  differs from local tested source. Do not claim the GitHub GUI is the same
  as the local candidate without pin reconciliation. A remote commit drift
  occurred during this work; local source was NOT force-overwritten.
- Whole Hermes installer remains **NO-GO**: 24 Python runtime modules are
  not published as an approved full distribution; MCP/Tunnel/Relay/Gateway,
  Yandex extension, trusted per-scope full-access OS enforcement, clean
  Windows 10/11 clone and Telegram ↔ ChatGPT end-to-end acceptance are open.
- Do not merge PR #6 or authorize a privileged route on this evidence alone.
