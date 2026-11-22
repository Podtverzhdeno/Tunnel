# Spec Delta — Correlated incident diagnosis without unsafe Send replay

## ADDED Requirements

### Requirement: Queue evidence must not become end-to-end completion

The system SHALL distinguish a queue metadata observation from actual agent/extension liveness, completed model output, authenticated browser session, exact loaded code, and Telegram delivery.

#### Scenario: Empty queue
- **GIVEN** a valid sanitized queue report with zero alerts
- **WHEN** triage runs
- **THEN** it reports no_queue_alerts_observed, not "healthy", and preserves explicit unverified delivery/completion flags.

#### Scenario: Queue observation unavailable
- **GIVEN** Relay SQLite, the tunnel, a required schema or a monitored source is not readable
- **WHEN** triage runs
- **THEN** observation_unavailable is reported, without asserting that any agent is dead, successful or idle.

### Requirement: Uncertain Send must remain reconciliation-required

The system SHALL NOT execute, approve or recommend blind retries after a possible mutating browser Send. It SHALL track uncertain history even if no new alert exists.

#### Scenario: New uncertain_send
- **GIVEN** an uncertain queue event with reason uncertain_send
- **WHEN** a sanitized monitor report is classified
- **THEN** it emits a P1 EXTERNAL_EFFECT_UNCERTAIN issue and reconcile_effect_without_replay action.

#### Scenario: Historical uncertainty hidden by baseline
- **GIVEN** previous baseline suppresses new-failure alert but the queue still has uncertain entries
- **WHEN** classification runs
- **THEN** UNCERTAIN_HISTORY_PRESENT is retained and not marked resolved.

#### Scenario: Missing post-Send confirmation
- **GIVEN** blocked send_unconfirmed_timeout or user_message_not_confirmed
- **WHEN** classification runs
- **THEN** explicit missing evidence is emitted and no new Send is authorized.

### Requirement: Request age is not worker death or phase latency

The system SHALL report stale/slow active requests using request created age and require independent liveness and phase receipts before declaring a worker stopped.

#### Scenario: Old claimed item
- **GIVEN** a claimed request older than threshold
- **WHEN** triage runs
- **THEN** the result calls for worker heartbeat and phase-receipt comparison, not automatic reassignment.

### Requirement: Structured privacy-limited failure hypothesis

The system SHALL reuse the canonical reason catalog, output only symbolic next steps, and never echo arbitrary report payload, tokens, URLs or browser transcripts.

#### Scenario: Known rate limit, auth or UI failure
- **GIVEN** known rate_limit, login_required or editor_not_found code
- **WHEN** classification runs
- **THEN** it selects a distinct bounded investigation action, with verified_root_cause=false.

#### Scenario: Unknown or malformed report
- **GIVEN** unrecognized alert, mixed-type keys, private reason string, NaN age or invalid counts
- **WHEN** triage is invoked
- **THEN** it fails closed and does not echo private content into the diagnosis.

### Requirement: No second sender or authorization bypass

The new triage subsystem SHALL remain a read-only projection, not modify Relay, Gateway, browser extension, owner approval broker, delivery state or trusted evidence.

#### Scenario: Many agents inspect concurrently
- **GIVEN** dozens of independent agents read queue snapshots
- **WHEN** triage runs
- **THEN** no queue/SQLite/browser/Telegram mutations, side-effect retries or shared source ownership changes occur.

### Requirement: A tested source is not automatically a deployed source

The system SHALL explicitly differentiate reviewed Git SHA, installed Windows source, loaded browser worker revision and real Telegram delivery.

#### Scenario: Older extension source in main
- **GIVEN** GitHub main manifest 0.6.6 and a local release described as 0.9.7
- **WHEN** a repair is proposed
- **THEN** source and runtime hashes must be checked before any installation or replacement; an offline test cannot be reported as live E2E.
