---
name: local-spec-workflow
description: Создавай проверяемые OpenSpec + GitHub Spec Kit-style SDD-спецификации для существующего проекта из ChatGPT с предпросмотром, требованиями SHALL и сценариями WHEN/THEN.
---

# Local Specification Workflow

## Activate when

The user requests a specification, spec-first planning, OpenSpec, SDD, a new feature change, or complete acceptance criteria in an authorized local project.

## Steps

1. Read `AGENTS.md` and `docs/SPEC_TOOL_GUIDE.md` in this project.
2. Identify the exact target project; do not work in the parent `IdeaProjects` Git root.
3. Inspect current project README and relevant code only via authorized scoped tools.
4. Derive testable capability-level requirements from the user's request; never invent facts about current implementation.
5. For every SHALL requirement include normal behavior, authorization/failure scenarios where relevant, and clear observable THEN.
6. Write a JSON manifest within the authorized workspace, following the guide's schema. Use only a unique kebab-case change identifier.
7. Invoke `spec_preview` using Local Workspace `run_script`; report target paths, requirements, scenario count and missing decisions.
8. Invoke `spec_request_prepare` with the manifest and plan SHA-256. Require the owner to review and approve in an actual local terminal via `owner_approve.py REQUEST_ID`; only then use `spec_request_execute`. Legacy `spec_create --apply` is disabled. Do not treat an agent-controlled flag or terminal simulation as authorization.
9. Verify actual OpenSpec `--strict` success, then run `spec_report PROJECT CHANGE --strict` from `ops.py` (or the standalone MCP `spec_quality_report`). Review SDD mappings, TBD design decisions, negative-case advisories and unresolved tasks before claiming the specification is ready. The report is static; it does not prove implementation or owner approval.
10. Add test evidence and request reviewer approval before implementation.

## Recovery when a spec job is stuck

- Inspect `spec_request_status REQUEST_ID` and `spec_request_diagnose REQUEST_ID` through existing `run_script` tools.
- If status is `reconciliation_required`, do **not** rerun file creation or change SQLite directly. Compare all planned hashes and strict validation.
- Only a human owner in a real interactive Windows Terminal may run `owner_reconcile.py REQUEST_ID`, review exact project and enter `RECONCILE <first-eight-id-characters>`. This tool only finalizes a verified ledger; it never writes project files.
- Partial/mismatched artifacts, noninteractive sessions and a changed artifact during confirmation MUST fail. Do not treat simulated TTY tests as real owner approval.

## Current limitations

- Native MCP tool registration is pending actual server access.
- `specify` CLI is not installed; the tool generates Spec Kit-style Markdown but does not run Spec Kit workflows.
- Approval is not yet enforced server-side for arbitrary local script execution; don't grant write tools to untrusted agents.

## References

`docs/SDD_ADOPTION.md`, `openspec/config.yaml`, and upstream sources in `docs/SPEC_TOOL_GUIDE.md`.
