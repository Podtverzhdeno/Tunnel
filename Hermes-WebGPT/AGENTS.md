# Hermes-WebGPT — standing user authorization

## Scope

The project owner explicitly granted standing consent in this conversation on
2026-11-11: work autonomously and treat changes to this project as approved by
default. This consent applies only to the Hermes-WebGPT project under the
configured projects workspace. It does not apply to sibling projects or the
Local Workspace MCP host.

## Autonomous project work

- Implement and improve Hermes-WebGPT features, fixes, project-local configuration,
  tests, diagnostics, specifications and documentation without repeatedly asking
  for chat confirmation.
- Complete the necessary implementation, focused tests, reviewable evidence and
  deployment preparation before reporting results.
- Reuse existing components and inspect current files and concurrent changes.
  Use SHA/CAS when replacing files and preserve other contributors' edits.
- Follow the owner's existing authorization to commit and push Hermes-related,
  reviewed source to Podtverzhdeno/Tunnel. Publish only an explicit file allowlist;
  keep real profiles, credentials, SQLite, messages and browser data private.
- Apply already-authorized updates to the identified Hermes components only after
  verifying the relevant source, process ownership and a suitable idle window.
  Preserve request identity, noReplay fences and user browser profiles.
- Report source installation, loaded runtime, tests and actual Telegram delivery
  as separate facts. Do not promise universal success from a single canary.

## Enforced approval mechanisms

Standing user consent is project guidance. It is not an authenticated local
terminal decision or an approval token issued by the existing specification
broker.

When a supported operation requires an enforced owner/host approval, use its
actual supported workflow. Do not fabricate a foreground terminal, supply fake
interactive streams, change approval records, weaken the shared approval
implementation or invoke lower-level writes to evade a pending/expired/denied
decision. Do not mark an operation approved or completed without real evidence.

The current specification broker supports per-plan local terminal approvals,
not a persistent per-project allowlist. Ordinary project work should continue
independently while such an operation is pending. Do not repeatedly regenerate
requests or ask for ordinary project permission merely because one gated step
is waiting.
