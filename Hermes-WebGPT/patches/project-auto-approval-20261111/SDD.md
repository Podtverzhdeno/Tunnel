# Optional project auto-approval — implementation specification

Owner request: allow an optional default approval for changes to a selected
project. This increment implements the existing enforced OpenSpec/SDD creation
gate; other permission systems remain independent. Status: implemented, installed
default-off and verified on Windows; owner opt-in is a separate action.

## Architecture decision

EXTEND_CANONICAL_OWNER: `spec_approval_queue.ApprovalQueue` already owns plan
verification, requests, durable execution, replay and recovery. Extend it with
project policy lookup inside its existing transactions. `owner_project_approval`
is a thin console adapter. Rejected alternatives: editing approved rows globally,
inferring grants from AGENTS.md, or adding a competing execution/approval broker.

Three additive tables hold exact directory grants, per-request grant bindings,
and metadata audit. Each enable creates a new grant ID. All unstarted request
bindings refer to one grant generation; replacing it cannot revive old requests.
The SQLite `BEGIN IMMEDIATE` transition is the execution linearization point.
Revocation committed before it blocks execution; revocation after it does not
claim to cancel already-started filesystem work.

## Requirements, scenarios and test crosswalk

| ID | SHALL requirement and WHEN/THEN acceptance | Tests |
| --- | --- | --- |
| FR-001 | The feature SHALL default off. WHEN no policy exists THEN prepare stays pending and manual approval works. | `test_policy_default_off`, legacy approval suite |
| FR-002 | The owner SHALL enable/disable through a foreground terminal with an exact project confirmation. WHEN either stream is noninteractive or the phrase differs THEN no grant is written. | `test_policy_requires_both_terminal_streams`, `test_policy_wrong_confirmation_no_mutation`, `test_noninteractive_cli_never_enables` |
| FR-003 | Grants SHALL match the exact canonical project and its directory identity. WHEN a sibling/child, alias or replaced directory is used THEN the grant does not apply. | `test_policy_exact_project_no_sibling_or_child_inheritance`, `test_alias_not_eligible_even_inside_workspace`, `test_windows_junction_rejected`, `test_directory_replacement_invalidates_policy` |
| FR-004 | Grants SHALL persist with default 24-hour validity, bounded custom hours, or explicit permanent mode. WHEN duration is invalid or grant expired THEN authorization fails. | `test_policy_default_duration_and_persistence`, `test_policy_duration_and_permanent`, `test_policy_invalid_options`, `test_policy_expiry_and_clock_rollback` |
| FR-005 | Only requests prepared under an active policy SHALL bind it. WHEN a policy is enabled after an old request THEN that old request remains unchanged. | `test_policy_not_retroactive`, `test_auto_failure_durable_not_retried_or_revived`, `test_manual_denial_supersedes_policy` |
| FR-006 | Revocation or replacement SHALL block unstarted bound requests. WHEN explicit per-request human approval exists THEN that independent approval remains valid. | `test_disable_revokes_unstarted_request_and_future_prepare`, `test_reenable_does_not_revive_old_binding`, `test_explicit_manual_approval_survives_policy_revocation` |
| FR-007 | Existing fresh-plan, manifest, TTL, exclusive-write and strict-validation guards SHALL remain enforced. WHEN manifest changes or request expires THEN no generation starts. | `test_auto_manifest_mutation_blocks_without_writes`, `test_request_ttl_still_enforced_with_permanent_policy`, `test_actual_spec_creation_and_strict_validation_in_temporary_project`, generator suite |
| FR-008 | Execution SHALL start at most once per request. WHEN concurrent calls race THEN one starts and the other returns in-progress. WHEN a completed request is replayed THEN its recorded result returns without new writes. | `test_parallel_execution_starts_only_once`, `test_auto_execute_once_and_replay_after_revocation` |
| FR-009 | Failure and stale-run reconciliation SHALL remain durable without automatic retry or deletion. WHEN a run fails THEN re-enabling cannot revive it. | `test_auto_failure_durable_not_retried_or_revived`, recovery suite |
| FR-010 | Migration and rollback SHALL preserve manual approvals and fail closed for auto requests. WHEN old code sees an auto-bound request THEN its stored pending state cannot authorize it. | `test_auto_prepare_is_rollback_safe_and_read_only`, `test_additive_migration_preserves_manual_approval` |
| FR-011 | Policy audit SHALL record metadata only. WHEN storage fails THEN CLI returns a typed redacted error. | `test_audit_contains_metadata_only`, `test_storage_error_redacted` |
| FR-012 | The owner console SHALL delegate to the existing queue. WHEN status is requested THEN it does not prompt or mutate grants. | `test_status_uses_existing_queue_and_no_terminal_prompt`, `test_enable_permanent_forwards_to_canonical_queue` |

## Implementation checklist

- [x] Extend queue schema and atomic prepare/execute authorization (FR-001–011).
- [x] Add enable/status/disable console with finite/permanent durations (FR-002,004,012).
- [x] Add negative cases and preserve manual/recovery compatibility (FR-001–012).
- [x] Document project bounds, expiry, opt-out and separate host permissions.
- [x] Install exact tested source with SHA preconditions and native-byte backup.
- [x] Verify the installed command reports default-off without enabling a grant.
- [ ] Owner may opt in from a real foreground terminal.

## Quality assessment and known limits

Security: default-off and exact scope; no new trusted actor claim. Reliability:
durable transaction and no replayed writes. Architecture: existing canonical
owner extended. Scalability: indexed primary-key lookups and bounded existing
request queue; audit currently grows with policy/request events, requiring normal
local retention planning if volume becomes high. Correctness: tests cover expiry,
revocation, migration, replacement and concurrency. Maintainability: stdlib only,
small CLI, no dependency installs. Performance: policy adds bounded SQLite and
directory-stat work, no network call or model turn. Observability: source-labelled
status and metadata audit. Traceability: FR/test crosswalk above.

This document is the feature's reviewed implementation specification in the patch
bundle. It does not forge canonical broker approval or create a grant. Formal
OpenSpec/SDD publication through the normal broker remains a distinct operation.
