# Proposal — Explainable read-only queue incident triage, 2026-11-22

**Baseline:** Podtverzhdeno/Tunnel main 350ea33080bc35245aa6741f85fa926cc8a8fcaf.

## Context

Hermes-WebGPT has a real Relay state machine, durable single-send intent and noReplay contracts, queue_monitor reason catalog v3, separate browser/CDP recovery work and parallel PRs. The missing capability is to explain whether a given signal supports a confirmed stop, uncertain effect, stale observation, or only a hypothesis; users cannot safely infer this from UI silence.

## Change

Add read-only incident_triage.py that consumes sanitized queue_monitor JSON and returns symbolic incident categories, urgency and safe investigative next steps. Add OpenSpec SDD/spec, offline fault tests and provenance of review. No duplicate queue, browser sender, terminal tool, Gateway process, retry engine or approval mechanism.

An older copy of Web Companion extension source in main has manifest version 0.6.6 while project documents refer to a locally installed 0.9.7. Loaded revision and runtime ownership were not inspected due to Local Workspace MCP Session terminated; do not deploy this branch over local runtime blindly.

A separate hermes-agent draft PR #9 introduces a generic typed observer, not yet joined to this Relay. Draft PR #1 there owns privileged task/effect approvals. Existing Tunnel PRs own CDP transport, Edge resource control, session recovery and result integrity. This change touches only new monitoring/SDD paths.

## Primary acceptance

- A queue with no alerts is called no_queue_alerts_observed, not healthy or complete.
- An unavailable observer is never misreported as a dead worker.
- An uncertain Send is reconciliation-required and never auto-retried, even if baseline hides repeat alert.
- Ages are request-creation ages, not current-phase durations.
- Reason codes are symbolic and sanitized; unknown input fails closed.
- No direct read of payload/result/lease/worker, no raw account or model text, no side effects.
- Python offline tests and a separate Windows-installed-source/regression gate before activating anything.

See design.md for the detailed SDD and the separate requirement scenarios in specs/correlated-incident-diagnosis/spec.md.
