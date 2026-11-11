# Spec Delta

## Purpose
Ограниченный cache уже найденных публичных доказательств для следующих model-only ходов.

## ADDED Requirements

### Requirement: Bounded public evidence cache
The system SHALL retain at most twelve public evidence entries for at most 300 monotonic seconds, keyed by the exact latest user request hash without storing raw private user text in the cache.

#### Scenario: Same task tool followup
- **GIVEN** public evidence was obtained on the initial user turn
- **WHEN** the same task returns a tool result within the TTL
- **THEN** the model-only view reuses a defensive copy with evidence age and cached-no-network attribution

#### Scenario: Bound or expiry
- **GIVEN** an entry expired, the monotonic clock moved backwards or the capacity was exceeded
- **WHEN** the cache is queried
- **THEN** the stale or evicted entry is not reused and reads do not extend its lifetime


### Requirement: No extra fetch or fabricated evidence
The system SHALL perform no new source fetch on a tool-only followup cache miss and SHALL preserve source failures and headline-only provenance.

#### Scenario: Cache miss
- **GIVEN** a tool followup has no matching evidence
- **WHEN** the model view is built
- **THEN** no network collection is triggered and no evidence is invented

#### Scenario: Insufficient source data
- **GIVEN** the original source collection returned no articles or timed out
- **WHEN** the evidence is reused
- **THEN** the same limitation remains visible and no claim of a new successful search is added


### Requirement: Canonical request preservation
The system SHALL keep canonical messages, real tool results, tool schemas and tool choice unchanged when adding model-only evidence.

#### Scenario: User changes task
- **GIVEN** a newer or different user message exists
- **WHEN** the model view is built
- **THEN** the previous request's evidence is not reused

#### Scenario: Mutation attempt
- **GIVEN** a caller mutates returned evidence
- **WHEN** a later model view is built
- **THEN** the internal evidence cache and canonical request remain unchanged
