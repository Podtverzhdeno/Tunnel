# Spec Delta

## Purpose
Exactly-one-intent per logical turn, persisted state and explicit post-send uncertainty rather than blind retries.

## ADDED Requirements

### Requirement: Pre-send durable intent
The system SHALL commit a unique turn_id, request_id, input digest, owned tab, active lease and pre-click intent record before allowing a web Send interaction.

#### Scenario: Pre-send durable intent accepted
- **GIVEN** The relay owns an unsent turn and its state has been persisted
- **WHEN** the worker authorizes a single UI Send
- **THEN** a durable intent receipt is committed before the UI action

#### Scenario: Pre-send durable intent failure boundary
- **GIVEN** The intent commit fails or its acknowledgement is unknown
- **WHEN** a click would occur
- **THEN** no additional Send is made until state is reconciled


### Requirement: No ambiguous-send retry
The system SHALL never automatically replay Send when click, browser, network, worker or result acknowledgement may have been lost after an intent; only observed pre-effect failures are retryable.

#### Scenario: No ambiguous-send retry accepted
- **GIVEN** A failed locator check conclusively happened before click
- **WHEN** bounded recovery runs
- **THEN** the worker may retry a fresh pre-effect preparation with the same fenced request

#### Scenario: No ambiguous-send retry failure boundary
- **GIVEN** An intent or click may already have occurred
- **WHEN** the worker reconnects
- **THEN** the attempt remains UNCERTAIN pending observation or user decision, without another Send


### Requirement: Saved user turn verification
The system SHALL verify that the user message visibly persisted in the exact owned conversation before treating a Send as saved, rejecting other chats or duplicate messages.

#### Scenario: Saved user turn verification accepted
- **GIVEN** The owned page displays one submitted user message matching the active turn
- **WHEN** the worker checks the transcript
- **THEN** it records a saved-turn receipt bound to exact chat and request

#### Scenario: Saved user turn verification failure boundary
- **GIVEN** The message is absent, duplicated or only visible in a different chat
- **WHEN** a saved event is proposed
- **THEN** the saved transition is withheld and an actionable diagnosis is recorded


#### Scenario: Attachment presentation confirmed by a bound completed reply
- **GIVEN** The owned transcript was empty at assignment and immediately before its single Send, and the persisted chat now has exactly one new authored user turn whose attachment does not echo the full prompt
- **WHEN** The composer is empty, no trusted user intervention occurred, and the latest assistant after that user is complete with a valid JSON envelope bound to the exact request_id
- **THEN** The worker may record one saved receipt regardless of prompt size; final delivery still requires a stable response and the existing Relay validation

#### Scenario: Unbound attachment evidence cannot authorize saved state
- **GIVEN** The prompt is represented by an attachment without a complete exact-text echo
- **WHEN** The reply is absent, malformed, still generating, bound to another request, precedes the user, or the transcript/user activity is ambiguous
- **THEN** The worker withholds the saved receipt, preserves the existing timeout and uncertainty policy, and never sends the prompt again


### Requirement: Final response evidence
The system SHALL accept a response only after observed stable final assistant-turn completion, bound to the saved user turn and chat identity; idle network or partial text alone is insufficient.

#### Scenario: Final response evidence accepted
- **GIVEN** A response completes in the same owned conversation with no generating indicator and stable final turn
- **WHEN** the worker extracts the permitted result under approved operation rules
- **THEN** it returns the exact bound text to the shared relay with a terminal evidence receipt

#### Scenario: Final response evidence failure boundary
- **GIVEN** The browser is still generating, reconnecting, or the assistant text is ambiguous
- **WHEN** the response timeout occurs
- **THEN** the result remains pending or uncertain and no fabricated success is returned
