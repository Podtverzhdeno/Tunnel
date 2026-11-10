# Verification evidence — 2026-11-11 (Asia/Amman)

Source installation is complete. Actual project opt-in is **not enabled**.
The installed status command returned `enabled=false, configured=false` for
`Hermes-WebGPT`; no real grant or per-request approval was fabricated.

## Results

| Environment / scope | Tests | Result | Evidence |
| --- | --- | --- | --- |
| Linux Python 3.12.14 / isolated candidate | 84 | 82 passed, 2 platform/dependency skips; rc 0 | `python run_isolated_tests.py` |
| Windows Python 3.13.5 / isolated candidate | 84 | 82 passed, 2 symlink-privilege skips; rc 0 | Local Workspace job `fe0fa6117f2540559a6bd461f21b765a` |
| Windows Python 3.13.5 / installed canonical modules | 84 | 82 passed, 2 symlink-privilege skips; rc 0 | Local Workspace job `f056bc11de1d469f8f17a3a8b2a1ab8f` |
| Installed owner status command | — | `operation=spec_create`, disabled/unconfigured; rc 0 | Local Workspace job `51575a06c4b047a9bec47f62b3ac1303` |

The Windows suite really created and strictly validated an OpenSpec + SDD change
in a temporary project, then replayed its recorded result. It also created a
Windows junction and verified the policy rejected it. Linux skips that Windows
check and the unavailable OpenSpec CLI. Windows skips two actual symlink tests
because the process lacks symlink privilege. These skips are not reported as passes.

Manual legacy approval/recovery tests, policy expiry/revocation, unchanged old
requests, exact project scope, directory replacement, no-overwrite guards,
rollback-safe stored pending state and concurrent execution were exercised.
Fixtures use temporary databases/directories. No Telegram message was sent and
no Gateway, Relay or browser restart was performed for this change.

## Source identity

| Installed file | SHA-256 |
| --- | --- |
| `Local-Workspace-Extensions/spec_approval_queue.py` | `0ae241408887d20777f4e283f2a9606971cde56c7650b1a4d8f84e3de18c92dd` |
| `Local-Workspace-Extensions/owner_project_approval.py` | `c3cfd21f6d9ffb319a9c1393a6013fda206e451f9f1fba713bd7c8d5d752027b` |
| `Local-Workspace-Extensions/test_project_auto_approval.py` | `df8069da1c852c4f3ace121722383cb7845814fc2e22a4e70ce09301bb3b0b92` |

Original broker SHA-256 was
`46574bf3f097d35145d45a97419a006e9f50b1ca47df1f2d24d3b9c7eb46d7ca`.
A native-byte backup is saved locally at
`Hermes-WebGPT/planning/project-auto-approval-20261111/spec_approval_queue.before.py`.
Installation used an exact SHA precondition. Production `ops.py`, `spec_tools.py`,
`owner_approve.py` and `approval_broker.py` were not overwritten. Dependency copies
in this bundle exist solely to reproduce isolated tests, not as competing owners.

## Remaining limit

The real owner enable/disable interaction has not been impersonated or claimed
as live tested. Console parsing and confirmation/decline are tested in temporary
fixtures. The owner can now enable the policy once in their actual terminal.
The feature governs SDD/OpenSpec only; broader host permission and OS identity
mechanisms are unchanged.
