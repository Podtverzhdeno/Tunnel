# Project-scoped OpenSpec/SDD auto-approval

Extends the existing `spec_approval_queue.ApprovalQueue`. There is no second
approval engine. The feature ships **disabled** and grants only `spec_create`
for the exact existing project selected by the owner.

## Owner commands

Run in your foreground Windows Terminal:

```powershell
cd C:\Users\user\IdeaProjects\Local-Workspace-Extensions
python owner_project_approval.py enable Hermes-WebGPT --permanent
```

Type `ENABLE Hermes-WebGPT` when prompted. This enables new OpenSpec/SDD requests
for that project until revoked. Omit `--permanent` for the default 24 hours, or
use `--hours 8` for a chosen duration (positive, at most 8760 hours).

```powershell
python owner_project_approval.py status Hermes-WebGPT
python owner_project_approval.py disable Hermes-WebGPT
```

Disabling asks for `DISABLE Hermes-WebGPT`. Status is noninteractive and does
not enable a grant. Enabling/disabling through noninteractive MCP execution is
rejected. No actual owner policy was enabled during development or tests.

## Agent workflow

Use the existing `ops.py spec_preview`, `spec_request_prepare`,
`spec_request_status` and `spec_request_execute` commands. No new MCP permission
argument or `--apply` shortcut is needed. New requests return `approved` with
`approval_source=project_policy` only when an effective owner policy was bound
at prepare time. Otherwise the existing `owner_approve.py REQUEST_ID` workflow
continues unchanged.

- Old pending, expired, denied or failed requests are not retroactively upgraded.
  Prepare a fresh request after a fresh preview; never repeatedly execute failures.
- A fresh preview hash, the 15-minute request TTL, no-overwrite creation, strict
  validation, durable state transitions and crash reconciliation remain mandatory.
- Policy is exact-project only, without inheritance by children or siblings.
  Directory replacement/rename, symlinks/junctions and expiry invalidate eligibility.
- Revoking/replacing a grant blocks unstarted bound requests. An operation already
  marked running may finish. A completed replay returns its recorded result and
  does not write files, even after revocation.
- A deliberate per-request terminal approval/denial supersedes its policy binding.
- Grants, bindings and audit metadata live in the existing local approval SQLite;
  do not commit this database. Audit stores identifiers/actions/timestamps, not
  prompts, message contents, tokens or credentials.

## Compatibility and security limits

Auto-authorized requests remain `pending` in the stored legacy request table.
Approval is derived from a separate current grant and checked atomically before
marking the request running. Rolling back to older broker code therefore denies
unstarted auto requests. Existing manual approval records are not rewritten.

This policy does not authorize arbitrary shell/admin actions, Telegram messages,
tool/plugin installation, reviewer acceptance or sibling projects. It preserves
the current terminal workflow; it is not a full OS security boundary against
same-user unrestricted scripts, database writes or hostile filesystem races.
The existing MCP host must enforce stronger identity/isolation separately.

The CLI is additive; `ops.py`, `spec_tools.py`, `owner_approve.py` and
`approval_broker.py` do not need production changes. Existing subprocess-based
`ops.py` invocations load the updated queue on their next invocation. Any separate
long-running consumer that already imported it would need its own normal reload.

## Reproduce verification

This patch folder contains dependency snapshots only to make its isolated test
suite reproducible; they are not new canonical implementations to install.

```powershell
python run_isolated_tests.py
```

All policy grants in tests use disposable directories/databases. `Terminal` is
test-only and must never be used for real projects. The suite covers legacy
approval/recovery behavior and the new policy. Real generator/strict-validator
integration runs when OpenSpec is installed. Platform-specific skips are reported.

Install only `spec_approval_queue.py`, `owner_project_approval.py` and the new test
into the existing Local-Workspace-Extensions project after checking original
source hashes. Preserve native-byte backups. Do not copy dependency snapshots
over current host code, remove the approval database, or create a project grant
as part of installation.
