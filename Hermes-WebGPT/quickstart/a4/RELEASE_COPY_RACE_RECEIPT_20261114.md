# A4 — Release manifest copy-race closure, 2026-11-14

## Risk and controlled reproduction
The GUI release installer initially parsed and pinned a source manifest,
then copied source GUI files, and later copied the manifest from that source
again without verifying the staged copy. A concurrent replacement could
leave a newly activated release with an invalid manifest, making the GUI
launch reject its active pointer.

Only synthetic local bundles were used; no live Hermes, browser, API keys,
Telegram or remote services were modified.

- RED witness: Local Workspace job
  `c6934d7eaa814ec8a542a76efaf3ebff`,
  rc=1, 2 expected FAIL: first install and upgrade after manifest replacement
  during the copy step.
- Fix: `quickstart/a4/release_manager.py` calls
  `_verify_staged_release(staged, version, expected_manifest_sha256)`
  after copying and before publishing version directory or pointer.
- Failure cleanup retains last verified active version and does not write a
  rollback marker or leave a stale stage/lock.

## Verified Windows test receipts
- Focused release cases: job `335b8778c02b4c52a4f470c32be2ca61`,
  returncode=0, 25 tests / 24 PASS / 1 SKIP.
- Full Quickstart: job `b20ace6d6dc24ca7b5b65d2f0c0ccde7`,
  returncode=0, 178 tests / 170 PASS / 8 SKIP.
- Local source `release_manager.py` SHA-256:
  `4d45edb3863e850c0b6b14bc119613d2eccb917723ab4c37e12ff68a9a6f0fb8`
- Local new test `test_release_copy_race.py` SHA-256:
  `bb79527ec7f21e3c0800bb8d428cf44070729fedff060361b8bf92a299989760`

## Distribution status
Updated selected A4 files in `Podtverzhdeno/Tunnel` branch
`hermes/a4-installer`, Draft PR #6. This is still only a GUI release
integrity fix, not a full clean-clone runtime, an owner-approved Tunnel
route, an OS-enforced full-access broker, or an independently accepted
Telegram ↔ ChatGPT E2E. Do not merge as a production-ready release.

Next gate: independent A5 audit of the complete installer and
privacy-reviewed dependency distribution before Windows 10/11 clean VM E2E.
