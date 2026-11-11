# Spec Delta

## Purpose
Доработка существующего queue_monitor вместо второго сервиса: типы ошибок и явные временные цели.

## ADDED Requirements

### Requirement: Typed redacted failure categories
The existing read-only queue monitor SHALL preserve supported browser/bootstrap/draft failure codes while mapping untrusted unknown reasons to other.

#### Scenario: Known browser failure
- **GIVEN** a new request is blocked by content_tab_not_ready or tab_create_unverified
- **WHEN** it is compared with the previous snapshot
- **THEN** the precise known failure is reported as new without exposing user data

#### Scenario: Private reason text
- **GIVEN** a reason contains arbitrary text, a URL or credentials
- **WHEN** a snapshot is emitted
- **THEN** the value is replaced by other and never included verbatim


### Requirement: Explicit latency target
The existing monitor SHALL optionally report an active request exceeding a configured response-age target separately from a stale-state alarm, using creation age rather than claiming phase duration.

#### Scenario: Slow active request
- **GIVEN** an active request exceeds the configured target but not the stale threshold
- **WHEN** the monitor runs
- **THEN** it reports a latency-target warning without declaring the worker dead or changing the request

#### Scenario: Completed request
- **GIVEN** a request is terminal
- **WHEN** the monitor runs
- **THEN** its creation age is not treated as current active latency


### Requirement: Windowed inference outcome report
The existing monitor SHALL report recent inference outcomes using an explicitly defined request-creation window, with unknown ratios when no terminal observations exist and without equating done with Telegram ACK.

#### Scenario: Recent outcomes
- **GIVEN** done, blocked and uncertain requests started inside the selected window
- **WHEN** the report is built
- **THEN** it reports counts and the inference completion ratio with its scope and time basis

#### Scenario: No delivery receipt
- **GIVEN** only queue metadata is available
- **WHEN** the report is built
- **THEN** Telegram delivery, content correctness and p50/p95 phase latency remain unverified
