# Durable recovery evidence (OpenSpec delta)

Status: Proposed; existing v0.6.6 Bridge source, not deployed.

## ADDED Requirements

### Requirement: Preserve first unsafe recovery evidence

The Web Companion Bridge SHALL preserve the earliest recorded event which
blocks browser reattachment, regardless of subsequent event count, while
retaining its original timestamp, stage and reason.

#### Scenario: Unknown effect followed by many benign events

**Given** an active completion-mode job with an authenticated diagnostic event
whose stage is unknown,

**When** at least 75 subsequent benign diagnostic events are recorded, and
a later send_clicked and message_saved are emitted,

**Then** the diagnostic list contains at most 60 events, retains the
original unknown event, and GET /v1/recover denies reattachment with
needs_reconciliation.

#### Scenario: Earlier and later unsafe events

**Given** a recorded tab_identity_unverified event,

**When** a newer tab_binding_disputed and more than 60 benign events occur,

**Then** the original blocker remains as the first persisted event and
recovery remains denied.

### Requirement: Fail closed on corrupted historical diagnostics

The Bridge SHALL NOT replace a pre-existing non-array, malformed JSON,
unreadable or structurally invalid diagnostic file with an empty array.

#### Scenario: Diagnostic corruption before event write

**Given** an existing diagnostics/job.json with damaged JSON,

**When** an authenticated /v1/event attempts to append a valid event,

**Then** the Bridge responds HTTP 409 diagnostic_history_unreadable,
does not report recorded:true, and leaves the damaged file bytes unchanged.

### Requirement: Retain privacy and bounded file format

The Bridge SHALL continue to write at most 60 metadata events with
only at,stage,reason keys and SHALL NOT record prompts, credentials,
ChatGPT conversation URLs, browser account details or secret tokens.

#### Scenario: Bounded privacy regression

**Given** a recorded unknown event,

**When** more than 100 valid benign events arrive,

**Then** each persisted event contains only allowed metadata and the original
blocking reason is retained without copying any private user content.

### Requirement: Recognize identity disputes as recovery hazards

The Bridge SHALL treat tab_identity_unverified and auth_context_changed as
recovery-blocking events, including when they precede a later saved turn.

#### Scenario: Account identity ambiguity

**Given** an otherwise apparently saved completion job,

**When** a tab_identity_unverified or auth_context_changed stage has been
recorded,

**Then** the Bridge responds needs_reconciliation, and does not authorize
automatic reattachment.

### Requirement: Evidence acknowledgement precedes successful response

A successful authenticated diagnostic event response SHALL follow the
write and replacement of the corresponding complete JSON array.
The Bridge SHALL preserve its current in-process lock and single-target-file
atomic-replace semantics, without claiming cross-process or power-loss
durability.

#### Scenario: Storage error

**Given** the file cannot be written or replaced,

**When** a diagnostic event is submitted,

**Then** no recorded:true success may be emitted and no recovery is
authorized from an empty replacement.

## Non-Requirements / limitations

- NOT full replayable event history.
- NOT guaranteed pre-click SEND_INTENT: old content script clicks before
  the best-effort telemetry send.
- NOT a new worker, browser/tab owner, transport, Relay queue or runtime
  installation.
- NOT permission to retry UNKNOWN external effects.
- NOT evidence of compatibility with installed v0.9.7 without G0 checks.
