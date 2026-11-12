# Hermes CDP fixture security review — v3

**Scope:** security hardening of a disposable **offline staging worker**, not a production agent. This package is not installed in the owner's Windows workspace, and no authenticated ChatGPT or Telegram E2E is claimed.

## Verified controls

1. Strict loopback URL: only literal IPv4 `http://127.0.0.1:<port>` with no userinfo, arbitrary path, query, or fragment; no system proxy and no HTTP redirect following.
2. Dedicated fixture challenge/response: 128-bit fresh nonce, HMAC-SHA256, exact schema, exact nonce, exact content-type, bounded response length; separate per-run proof key and scoped worker bearer.
3. Refusal of spoofed `Host`, stale response, wrong signature, invalid proof secret, external host, and all HTTP redirect codes including 301/302/303/307/308.
4. SHA-pinned staging-only install with exact source anchors and two imported proof modules; compile validation; no root Relay/Gateway modifications.
5. Atomic publish using same-volume `os.link()` for backup and new files. No hardlink fallback on unsupported volumes. Existing sources are updated via `os.replace()` only after digest checks, test source first and worker source last.
6. Compensating rollback for controlled exceptions. Each rollback step checks that destination still has *exact installed bytes* before restoring originals. Externally modified files remain untouched; explicit conflict requires human inspection.
7. Random marker on exclusive lock, inode check before cleanup, stale/foreign lock preservation. Corrupted lock prevents reporting successful installation.
8. Fault-injection tests exercise before/after-effect failures, an unsupported hardlink volume, injected postcommit error, concurrent edits, stale lock, lock tampering, source SHA drift, and symlink refusal.

## Explicit residual limits

- **No arbitrary process/site authentication.** The per-run HMAC only distinguishes a test Relay intentionally initialized with the secret. A stolen secret compromises the proof.
- **No atomic multi-file commit or crash-recovery service.** In particular `os._exit` or a power loss may strand partial staging files and the lock. In that case do not run the worker; reconcile file hashes against backups and review the source before proceeding.
- **No linearizable compare-and-swap for concurrent foreign writers.** Digest checks occur immediately before replacement but a process ignoring the staging lock can race between check and rename. Conditional rollback refuses to overwrite observed foreign bytes; it cannot prevent an adversarial microscopic race.
- **No Windows integration proof.** Unit tests run in a Linux container. Windows NTFS `os.link` / ACL / Edge specifics still require testing on the target host. Failure to support hardlinks is a safe installation failure, not permission to enable unsafe fallback.
- **Not a production CDP worker.** Existing Telegram, browser extension, authorized ChatGPT profile, Relay delivery, tool calls, privacy filters, and real Telegram SendResult receipts are outside this patch.

## Test evidence

Command from the extracted package directory:

```
python -m unittest -v test_fixture_relay_attestation.py test_apply_guarded_patch.py test_checked_rollback.py
```

Latest local run: **37 passed / 0 failed / 0 skipped**; raw output is in `UNIT_TEST_RESULTS.txt`. Syntax and ZIP integrity are separately validated at packaging time. The archive and SHA manifest are for integrity, not a digital publisher signature.

## Recommended deployment decision

- **Staging only, after source SHA inspection and local host availability.** Refuse to install when any target source differs.
- Disable/avoid auto-claiming real queued messages until owner profile and active Send fence audits are independently verified.
- Keep live extension route unmodified and require a separate, request-bound ChatGPT Web canary plus Telegram final SendResult ACK before asserting end-to-end delivery.
