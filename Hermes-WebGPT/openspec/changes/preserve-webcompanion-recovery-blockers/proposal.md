# OpenSpec: Preserve no-replay evidence in bounded Web Companion diagnostics

Status: DRAFT, not deployed. Baseline 2026-11-22: `main` at
`350ea33080bc35245aa6741f85fa926cc8a8fcaf`.

## Problem

The v0.6.6 Web Companion `bridge.py` stores at most 60 metadata events per
task: `previous = (previous + [item])[-60:]`. Meanwhile the read-only
`recovery_candidate()` checks that same tail for historical warnings:
`unknown`, `report_disputed`, `tab_preserved`,
`tab_binding_disputed`, `account_context_changed` and
`unexpected_close`. The original warning can be dropped by many subsequent
benign events. A later valid-looking `send_clicked` plus `message_saved`
pair might therefore make a previously disputed task appear eligible
for browser reattachment.

This is a source-level safety defect. We have NOT observed such a sequence on
the owner's actual Windows installation. That installation's extension may
be v0.9.7 while the public source manifest reports v0.6.6.

A second source-level problem is that an existing malformed diagnostics JSON
file is silently overwritten with a new list. Erasing evidence on parser
failure is not a safe recovery behavior.

## Change scope

Modify only the old Web Companion **local bridge's bounded metadata recording
and existing recovery decision**; no browser Send, Relay, Telegram, Gateway,
MCP authority, profile, credentials, installer, UI or approval bypass.

- Keep each per-job diagnostics JSON as an array with up to **60** events.
- Retain the earliest recognized no-replay/recovery-blocking event *in the
  same replacement JSON document*, using one slot when necessary.
- Preserve the last 59 events and original `at/stage/reason` fields,
  never synthesize a second event or alter its original timestamp.
- Treat `tab_identity_unverified` and `auth_context_changed` as recovery
  blockers, matching the background worker's existing identity checks.
- Reject malformed existing diagnostics without overwriting them.
- Prepare each JSON replacement and fsync its bytes before sending successful
  HTTP acknowledgement; preserve the existing in-process lock.
- Add real-bridge regression tests for 75+ events, later send/saved,
  corrupt input, conflicting identity and fixed privacy schema.

## Invariants

S1. Historical post-Send uncertainty cannot disappear merely because new
telemetry arrives, nor become a legitimate auto-replay authorization.

S2. An unparseable existing log blocks diagnostic append, not a silent reset.

S3. Diagnostic retention remains bounded; no prompt, browser account, cookie,
Telegram message or private URL is added to events.

S4. The event write uses one target-file `os.replace` for recent and sticky
evidence; when it returns success the target reflects a complete JSON array.
We do **not** claim full disk-power-loss durability, cross-host ordering, or
that events lost before HTTP submission can be recovered.

S5. Existing completion report SHA/ACK and owned-tab checks remain unchanged.
The bridge must not automatically resume or re-send an uncertain job.

S6. This is a safety hardening of **v0.6.6 source**, not an automatic patch to
the separate installed Windows v0.9.7. Runtime identity + idle window + owner
approval gates still apply.

## Out of scope / known residual risk

The content script currently invokes `button.click()` before sending
`send_clicked` telemetry; the service worker forwards telemetry best-effort.
A crash before event acknowledgement can leave a gap not repairable by sticky
history. A future version must use a durable acknowledged intent **before**
clicking Send and a reconciler for uncertain outcomes, with no automatic
re-send. Adding such a protocol is a separate coordinated rollout, not a
silent change to old clients.

No new cross-process atomic event store, multi-agent queue, background
service or broader causal graph is added here. The parent SDD documents
the necessary separate work.

## Release gate

1. Existing `test_bridge.py` suite and new tests pass, plus syntax/import
   verification with exactly pinned source SHA.
2. Parallel agents confirm that no other PR owns `bridge.py` or this
   test file. Current PR #10/#11 touch background worker, **not** bridge.py;
   keep compatibility checks before merge.
3. Run complete Web Companion Node/Python suites and native browser fixture.
4. Compare installed v0.9.7 code with this v0.6.6 bridge; do not overwrite
   an unrelated installed bridge.
5. Controlled one-job recovery drill with the appropriate owner approval,
   exact restart/reload state and **no actual re-send** of uncertain work.
6. Retain independent receipts. No claim of production behavior before G0.

Full architecture and fault model: 
`Hermes-WebGPT/docs/ATOMIC_OBSERVABILITY_SDD_20261122.md`.
