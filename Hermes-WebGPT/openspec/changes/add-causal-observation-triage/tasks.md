# Tasks — Hermes queue incident diagnosis

- [x] Inspect actual Hermes-WebGPT Relay, queue observer, Web Companion manifest and existing OpenSpec/SDD.
- [x] Identify independent state/evidence layers and do-not-replay ambiguity.
- [x] Build read-only pure incident_triage.py on top of existing sanitized queue reports.
- [x] Test missing observation, historical uncertainty, auth/rate limit/UI and manual input categories, stale age, secret redaction, malformed types and output certainty.
- [x] Write formal SDD, proposed architecture, source ownership and Given/When/Then tests.
- [x] Run 29 focused synthetic tests against mirrored current reason catalog offline; syntax checks.
- [ ] Re-run new tests and existing monitoring regressions in actual connected Tunnel source on Windows (MCP session unavailable).
- [ ] Verify installed Web Companion 0.9.7 source/loaded SHA and Relay/Gateway PID before applying any executable changes.
- [ ] Verify exact request-to-chat and Gateway-to-Telegram delivery with owner-approved E2E fixture.
- [ ] Review bridge to hermes-agent PR #9 and Autopilot ledger PR #1 and prevent duplicate state machines.
- [ ] Require collector authentication, replay guard, privacy, E2E fault injection and independent verifier before autonomy.
- [ ] Keep genuinely gated owner/host terminal approvals enforced; do not assume file-based consent is an approval token.
