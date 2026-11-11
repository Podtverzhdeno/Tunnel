# Implementation tasks — Hermes: готовность браузерного инструмента, сохранение источников между ходами и наблюдение задержек

Status: generated plan; no implementation is claimed.

- [ ] 0. Review and approve this proposal, requirements and acceptance scenarios.
- [ ] 1. Audit current project implementation, data model and trust boundaries.
- [ ] 2. Finalize design, rollback and test fixtures.
- [ ] T001 Implement and test windows-browser-tool-readiness / Native launcher selection; include negative cases, outputs and error contracts.
- [ ] T002 Implement and test windows-browser-tool-readiness / Existing browser fallback; include negative cases, outputs and error contracts.
- [ ] T003 Implement and test windows-browser-tool-readiness / Truthful activation evidence; include negative cases, outputs and error contracts.
- [ ] T004 Implement and test followup-source-continuity / Bounded public evidence cache; include negative cases, outputs and error contracts.
- [ ] T005 Implement and test followup-source-continuity / No extra fetch or fabricated evidence; include negative cases, outputs and error contracts.
- [ ] T006 Implement and test followup-source-continuity / Canonical request preservation; include negative cases, outputs and error contracts.
- [ ] T007 Implement and test response-quality-observation / Typed redacted failure categories; include negative cases, outputs and error contracts.
- [ ] T008 Implement and test response-quality-observation / Explicit latency target; include negative cases, outputs and error contracts.
- [ ] T009 Implement and test response-quality-observation / Windowed inference outcome report; include negative cases, outputs and error contracts.
- [ ] FINAL Run local spec crosswalk and installed OpenSpec strict validation.
- [ ] FINAL Run unit, negative, and E2E acceptance checks with evidence.
- [ ] FINAL Reviewer approval and explicit release/rollback decision.
