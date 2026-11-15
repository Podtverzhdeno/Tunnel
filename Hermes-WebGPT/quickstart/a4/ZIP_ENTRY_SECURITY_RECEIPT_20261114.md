# A4 — ZIP entry type security receipt (2026-11-14)

## Scope
Offline source runtime ZIP verification only. No install, activation, cloud
connector registration, browser manipulation, Telegram send or real credentials.

## Reproduced vulnerability
`verify_candidate` previously checked ZIP entry names and SHA-256 content,
but did not reject entry *type* metadata. A candidate with unchanged module
bytes and manifest could mark `relay.py` as a Unix symbolic link, FIFO, or
Windows DOS directory (without a trailing slash). The stager also accepted
the metadata if the caller provided that archive's SHA-256.

Negative regression receipts before fixes:
- Job `5c855bb7c7c14d77ad3b1490eb26329e`: 3/3 expected FAIL,
  returncode 1 (Unix symlink, FIFO, stager admission).
- Job `8c4a3699daed4403b8bcfb6ee8c3e705`: 1/1 expected FAIL,
  returncode 1 (DOS directory attribute).

## Fix and proof
- `runtime_bundle.py`: reject ZIP directory entries, encrypted entries,
  oversized entries, Unix types other than regular/no type, and the DOS
  directory attribute before accepting the candidate.
- `test_runtime_zip_entry_security.py`: four negative cases including
  a stage refusal; all archives contain synthetic source data.

Pinned local SHA-256:
- `runtime_bundle.py`: `76bf6117de27403f579726d2da6df033cd62253986a22fea3a3d0ca976fe3433`
- `test_runtime_zip_entry_security.py`: `f790b22ab46b5311c9367363d199efc61c81ce2e6c68036574d24e98b6effb74`

Windows Local Workspace results:
- Focused tests: job `8f880f5657384410b6a3a93a4e387b08`,
  returncode 0; 25 total, 23 PASS, 2 SKIP.
- Entire Quickstart: job `6490782fddbf4bb485e80d19ffc74dce`,
  returncode 0; 170 total, 162 PASS, 8 SKIP.
- Earlier full run `2d1972bfd909427db24513c84d1809b5`
  had 2 failures in concurrent, not-yet-reconciled `extension_contract`
  changes; later run above is GREEN against their corrected source.

## Acceptance boundary
**ZIP entry type admission hardening is complete locally**. This is not
independent A5 acceptance of Hermes A4, a signed public package, a fresh-clone
installation, or a live Telegram ↔ ChatGPT E2E proof. No public GitHub sync
or production installation was performed by this patch.

Next A4 gate: independent source/zip review, selective Draft PR #6 sync,
clean Windows 10/11 install of the complete dependency set, trusted access
boundary and browser/Telegram E2E. Never bypass owner-granted Tunnel identity
or OS-level full-access approval.
