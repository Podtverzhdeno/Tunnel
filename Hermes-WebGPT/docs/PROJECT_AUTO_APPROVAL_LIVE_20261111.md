# Live project auto-approval verification — 2026-11-11 (Asia/Amman)

The owner enabled the Hermes-WebGPT policy in their actual foreground terminal.
A fresh status probe confirmed enabled=true and expires_at=null. No approval
streams or decision records were fabricated.

The old request 9d2703729c56fa1015f4730baa9971a9 is expired and was not revived.
A new preview of the response-quality feature was required. The first plan,
request ce6de9f3b9d7d3f0a5fa94e973caf5fc, was automatically approved but then
rejected PREVIEW_CHANGED before generation: a parallel onboarding change occupied
SDD index 003. Read-only diagnosis found all ten planned files missing; no files
were deleted, overwritten or retried under that obsolete request.

A fresh preview selected index 004. Request 3b86339de9270297fe572775b80f9def
was prepared with approval_source=project_policy and independently checked as
approved. Execution returned created=true, replayed=false. All ten planned files
were created through the normal broker. Plan SHA-256:
6de6a57ae7a665e9bbc6fee87fb9e1bae4e9f9a3f7e1245e517bc9619e41e8ac.

Creation and a separate spec_validate both passed OpenSpec strict and the SDD
crosswalk: 9 requirements, 18 scenarios, 9 FR mappings. Canonical SDD:
specs/004-improve-hermes-browser-readiness-and-source-continuity/spec.md.
OpenSpec: openspec/changes/improve-hermes-browser-readiness-and-source-continuity/.

Real Local Workspace jobs (all successful unless explicitly noted):
- Owner policy status: b93e04085b8a4fbdbbf94b7cca0b0770, rc 0.
- Fresh preview after concurrent index change: 36538161f8e94aab8bc8bcf65942f16c, rc 0.
- Prepare: ef795b1b978340aab7da70979338e0da, rc 0.
- Status approved: 4ec09b3a64ba4e218ad16630dc16f4e8, rc 0.
- Create: 90ad24acf8f549f9a55586460992707f, rc 0.
- Separate strict validation: 6d2ddc3a64114fc8bd6d1708081a1de6, rc 0.

This closes the canonical-creation blocker recorded in the earlier response-quality
checkpoint. It does not claim a new runtime deployment, Telegram E2E delivery or
formal implementation acceptance. The generator's impact/design/plan templates
still contain five TBD markers; the detailed prior engineering review remains at
docs/RESPONSE_QUALITY_SDD_20261111.md. Existing task checkboxes remain unchecked.
The new manifest changes only the approval-workflow constraint to acknowledge the
owner-enabled policy; the nine functional requirements are preserved.

The capability continues to apply only to OpenSpec/SDD creation in the exact
Hermes-WebGPT project. It does not grant sibling/child projects or bypass host,
reviewer or arbitrary-tool permissions. No grant ID or live database is published.
