# SDD — Hermes × GPT cross-repository causal evidence contract
Date: 2026-11-22. Status: PROPOSED / non-authoritative / read-only design. Owner: unassigned integration reviewer. Scope: Podtverzhdeno/Tunnel (Hermes-WebGPT / Web Companion), Podtverzhdeno/hermes-agent. No live Windows process, installed browser extension, Telegram or ChatGPT session inspected during this review.

## 1. Why this document exists
There are 32 open PRs in hermes-agent and 17 open PRs in Tunnel at the observed snapshot. Multiple teams are independently modifying effect handling, event journals, CDP ownership, and multi-agent CI. A green local observer cannot prove that a browser action was delivered, that ChatGPT completed, or that Telegram received an answer. A source-level review is not a live-incident report. The critical missing agreement is a **cross-component evidence contract** and one integration decision rather than another competing scheduler, journal, or monitor.

Source evidence pinned at audit time:
- Tunnel main tree `350ea33080bc35245aa6741f85fa926cc8a8fcaf`; `chat-launch-smoke-desktop-test/web-companion/bridge.py` blob `eaf301ac15ad301464159438d6431cbc91d3e323`; `Hermes-WebGPT/monitoring/queue_monitor.py` blob `c22e457ec2e37f3a6fa9b8c411c7185c9bcddecd`; `Hermes-WebGPT/relay.py` blob `9c30cbe864556fbf497ed2dea6619c58fd7156ff`.
- Hermes fork main `7e9100901819ee44c16b4ddcb79a6bcb7909f591`.
- Relevant competing proposals: Hermes PRs #4/#5/#6/#11 (gateway handler), #12/#17/#23 (companion), #25/#28/#30 (audit/coordination), #13/#15 (external CDP); Tunnel PRs #11/#17 (browser lifetime), #13/#19 (monitor), #14 (recovery evidence), #15 (waiters), #16 (mirror).
- Source observation: `bridge.completion_client_ready()` checks a 0.6.6 heartbeat as a compatibility signal and explicitly disclaims authenticated browser/account identity; `recovery_candidate()` derives an eligibility result from file-backed events; Relay queue monitor selects only request metadata and uses SQLite read-only mode. None establishes a verified external effect receipt.
- External background: Chrome Manifest V3 service workers can be suspended/restarted; SQLite WAL provides snapshot isolation, not end-to-end distributed atomicity; OpenTelemetry messaging conventions support span/context links, not a delivery guarantee. See references at bottom.

## 2. Hard safety invariants (MUST)
I1. No inference from silence: no event or timed-out request may be interpreted as proof that Send did not happen.
I2. Browser click, network acceptance, model completion, report persistence, Hermes tool effect, and Telegram delivery must be distinct milestones with distinct evidence.
I3. A new attempt MUST NOT clear external effects reserved by an older attempt. An unresolved side effect blocks automatic redispatch of the same effect identity.
I4. A telemetry emitter may attest only what it actually observed. An event of type `send_clicked` must not be upgraded to an authenticated provider receipt.
I5. Only the native authoritative scheduler/effect gateway may change task state. Read-only observers produce diagnostics, never authorize retry, resolve unknown, or unlock privileged actions.
I6. Worker timeout, OS process disappearance, service-worker suspension, tab re-navigation and human cancellation are independent causes. None implies the other.
I7. All process-local IDs must be qualified by producer identity AND incarnation; a source category such as `browser` is not a unique producer.
I8. No agent may merge another agent's branch, overwrite a shared SDD, or close a safety blocker using only its own synthetic tests.
I9. Event logs MUST NOT contain prompts, user text, response bodies, cookies, bearer tokens, local paths, full conversation URLs or raw DOM. Tokens must remain externally stored.
I10. An unavailable or truncated evidence source requires an explicit `coverage=partial|unknown`; success cannot be inferred from a sampled last-N window.

## 3. Canonical evidence envelope v1 — future protocol, not currently deployed
Fields are *proposed*, and may require migration; no code currently emits all of them:
```json
{
  "schema": "hermes.gpt.evidence.v1",
  "run_id": "opaque-server-generated-id",
  "job_id": "opaque-scope-id",
  "attempt": 2,
  "producer_id": "stable-pseudonymous-worker-id",
  "incarnation_id": "new-random-id-on-every-restart",
  "seq": 17,
  "event_id": "opaque-dedup-id",
  "caused_by": ["previous-event-id"],
  "stage": "browser.send_observed",
  "observed_at_utc": "RFC3339 timestamp",
  "monotonic_ns": 123456,
  "fence_token": "opaque-epoch-token",
  "authority": "untrusted_telemetry",
  "coverage": "complete_since_incarnation",
  "effect_id": "stable-id-for-the-logical-side-effect",
  "source_build_digest": "sha256-of-runtime-artifact"
}
```
No actual authorization secret or trace payload belongs in these fields. Identifiers are pseudonymous and scoped. `monotonic_ns` is comparable only within the same process incarnation. The `fence_token` may be recorded only in a privacy-safe form and is not itself proof of a valid host-issued lease. Causal ordering across emitters requires explicit `caused_by` or authenticated receipts, NOT naive timestamp sorting. If the upstream native Runs API supplies its own canonical identifiers, map to them, do not mint a competing run owner.

## 4. Evidence authority ladder
L0: request created in local queue — proves only local intent.
L1: browser worker claimed / editor filled — proves only untrusted local observation.
L2: click initiated or DOM Send attempted — **external effect may already exist**, but delivery remains unknown.
L3: user message observed in the same verified conversation / correlated provider observation — stronger evidence, still not general proof of account identity unless authenticated.
L4: assistant output observed and bound to that exact turn — evidence of visible completion, not Telegram delivery.
L5: report durable with digest and durable local ACK — evidence of local persistence, not remote delivery.
L6: target transport returns durable message ID / authenticated delivery receipt — evidence for that transport, only.
A timeout at L2..L5 must preserve `effect_outcome=unknown` unless reconciled with a trusted source. Do not collapse statuses into a single success flag.

## 5. State projection and failure diagnosis
Separate three dimensions:
- **Run control**: requested / admitted / executing / stop_requested / terminated.
- **Effect certainty**: none / reserved / may_have_happened / proven / reconciled_not_applied / disputed.
- **Observation quality**: complete / partial / truncated / source_missing / contradictory / untrusted.
Do not derive one dimension solely from another.

Reason codes and response:
| Code | Minimum evidence | Interpretation | Safe operator action |
|---|---|---|---|
| `SEND_EFFECT_UNKNOWN` | browser Send attempted, no trusted correlated receipt | duplicate risk | stop automatic repeat, reconcile exact turn |
| `MV3_INCARNATION_LOST` | heartbeat gap and new worker incarnation | service worker may restart | reload durable state, do not assume unsent |
| `EVENT_WINDOW_TRUNCATED` | rolling 60-event export without full prefix | missing causal history | request bounded archive/checkpoint; do not claim complete |
| `PRODUCER_SEQ_GAP` | contiguous producer incarnation sequence missing | dropped or reordered evidence | fetch gap; preserve unknown |
| `TERMINAL_CONTRADICTION` | same attempt with credible complete/failed claims | inconsistent outcomes | quarantine, independent review |
| `FENCE_STALE` | worker epoch older than authoritative lease | obsolete worker | reject new execution; retain prior effect evidence |
| `EXTERNAL_BROWSER_UNOWNED` | CDP process attached but not created by Hermes | cleanup unsafe | release tracking only; don't kill external browser |
| `REPORT_ACK_ONLY` | local report ACK but no Telegram receipt | false delivery risk | preserve local success, mark transport pending |
| `SOURCE_BUILD_MISMATCH` | installed manifest/runtime hashes differ from reviewed tree | tests not applicable | block release; inspect installed artifacts |
| `BASE_HEAD_MOVED` | PR base SHA/head changed since evidence | invalidated CI/approval context | rerun merge-base and exact-HEAD checks |

Maintain `diagnosed` versus `hypothesized` versus `not_observable` explicitly. A typed reason is not automatically a proven root cause.

## 6. Atomic effect protocol and fencing
1. Authoritative scheduler inserts a durable unique effect intent (`effect_id,run_id,attempt,lease_epoch,operation_kind,hash_of_redacted_metadata`) in the same local DB transaction as its state transition. **This is not a transaction with ChatGPT or Telegram.**
2. Before an external action, verify the current lease/epoch. Record a durable `dispatch_started` transition. Do not log raw prompt or external auth headers.
3. After the action, persist only a trustworthy correlated receipt, or transition to `may_have_happened`. A network exception after side-effect dispatch is UNKNOWN.
4. On restart, reconcile unknown effects against authenticated provider state where supported. If no trustworthy evidence exists, require operator reconciliation. Never make a fresh attempt silently replace old unknown evidence.
5. Repeat only when effect is proved not applied and action remains permitted; or an upstream service implements an independently verified idempotency key for exactly this operation.
6. Report delivery is a second effect with a distinct `effect_id`; model completion does not imply message delivery.

SQLite unique constraints and transactions can coordinate local processes; they do not provide cross-host exactly-once. Use explicit distributed ownership and CAS/fencing only after the authoritative scheduler selection.

## 7. Thirty-to-one-hundred-agent coordination
- Choose exactly ONE: execution authority, idempotency/effect ledger, browser ownership service, source-of-truth event schema, PR collision guard, integration reviewer. Candidate observers may remain as independent **test oracles**, never competing producers of task truth.
- Every agent proposal declares `scope_paths`, `base_sha`, `head_sha`, `upstream_dependency_prs`, `schema_changes`, `owned_invariants`, `read_only`, `effects_possible`, `verification_artifacts`.
- Intersect filenames AND semantic contract ownership. Different files can redefine the same status or reason-code semantics. Renames require old+new path comparisons.
- Stacked PRs must be compared against their true parent head, not evaluated as independent children of main. If parent changes, invalidate dependent test claims until integrated on the new parent.
- Do not let a draft, missing check, skipped paid review, older green SHA or partial CI count as an approval.
- A no-op review may improve quality more than a thirty-third conflicting implementation; issue a source-pinned diagnostic rather than another runtime fork.

## 8. Prioritized candidate improvements and strict evaluation
Scoring rubric (each 0–5): safety impact S, causal diagnosis D, feasibility F, interoperability I, operational cost C (higher = cheaper). Weighted provisional score = 0.35S+0.25D+0.20F+0.15I+0.05C. Scores are architectural estimates, NOT measured performance.

| Rank | Proposal | S/D/F/I/C | Score /5 | Gate |
|---|---|---|---:|---|
| 1 | Unified cross-component evidence envelope + authority labels | 5/5/4/5/4 | 4.75 | offline contract/compat tests |
| 2 | Preserve unknown external effects across attempts | 5/5/4/4/3 | 4.55 | crash-after-click and retry race |
| 3 | Runtime/source build attestation incl. installed MV3 manifest | 5/4/4/5/4 | 4.50 | installed SHA/extension mapping |
| 4 | One selected authoritative scheduler/ledger | 5/5/3/5/2 | 4.45 | migration/rollback + concurrent tests |
| 5 | Scoped CDP ownership and tab identity fencing | 5/4/3/4/3 | 4.10 | external Edge + second-agent E2E |
| 6 | Bounded causal archive with gap detection | 4/5/4/4/3 | 4.20 | eviction, restart, corruption tests |
| 7 | Operator incident timeline with confidence/unknown fields | 4/5/4/4/3 | 4.20 | no false-green scenarios |
| 8 | More polling / forced browser keepalive | 1/2/3/2/1 | 1.80 | REJECT until power/CPU test |

The ranking is not an authorization to deploy any proposal. Upstream/native Runs capability and installed version must be verified before extending the fork's scheduler.

## 9. Minimum chaos matrix (must be executed on exact release candidate)
T01: killed MV3 worker after click but before `send_clicked`; expected UNKNOWN, no automatic repeat.
T02: browser click acknowledged locally but report write fails; UNKNOWN preserved.
T03: crash between durable intent and dispatch; no assumed result, reconcile.
T04: crash after dispatch before receipt; side effect remains uncertain.
T05: external browser tab navigated to another conversation before cleanup; must not close unrelated tab.
T06: second agent shares browser; first agent cleanup cannot kill shared Edge.
T07: producer restarts sequence at 1; incarnation disambiguates.
T08: two producers report conflicting terminal events; contradiction visible.
T09: last-60 event export loses earlier blocker; coverage cannot become complete.
T10: clock moves backward 10 minutes; no timestamp-only causal ordering.
T11: one agent's idempotency request is cancelled during another's in-flight work; work outcome preserved.
T12: 50 agents submit same effect ID concurrently; one authoritative reservation, no second dispatch.
T13: 50 agents submit different IDs under bounded capacity; overload reason explicit.
T14: old worker publishes late ACK after new attempt; stale fence cannot authorize release.
T15: successful assistant output but Telegram delivery times out; separate effect UNKNOWN.
T16: installed extension version mismatches GitHub source; release blocked.
T17: sibling stacked PR changes parent SHA; exact-HEAD gate invalidated.
T18: observer drops all events; dashboard shows SOURCE_MISSING, never all green.
T19: SQLite WAL reader races with writer and checkpoint; read-only observer stays internally consistent, partial exposure explicit.
T20: compromised/untrusted browser telemetry claims remote receipt; trust boundary prevents upgrade.
Each test must record input fixture SHA, target code SHA, expected typed state, observed state, and failure explanation; synthetic runs must be labeled SYNTHETIC.

## 10. Integration gates / decision log
- G0: record installed Windows Hermes executable and browser-extension manifest *hashes*, not secrets. NOT MET in this audit.
- G1: select canonical native Hermes Runs/API capability and version, compare with fork; no parallel scheduler. NOT MET.
- G2: assign owners for browser lifetime, external effects, observer, gateway and merge controller. NOT MET.
- G3: integrate and review one event schema. NOT MET.
- G4: execute T01–T20 on exact head + rollback rehearsals + 30/50/100-agent load/fault tests. NOT MET.
- G5: independent human approval and controlled release window. NOT MET.
Decision D01: **do not auto-merge existing overlapping PRs or attempt a live repair by polling alone**.
Decision D02: prefer metadata-only, bounded, no-side-effect diagnostics until trust and authority are established.
Decision D03: do not treat the project's existing `AGENTS.md` standing-consent statement as a substitute for host-enforced local approval.

## 11. Audit log and uncertainties
- 2026-11-22 observed both GitHub repositories, main SHAs, PR inventories and selected main source blobs.
- Confirmed from bridge source: heartbeat 0.6.6 compatibility check is not authenticated identity; recovery depends on local file events. Confirmed from queue monitor source: metadata-only read-only SQLite observer. These are source facts, not witnessed incidents.
- Confirmed from repo state: multiple unmerged overlapping PRs; tests claimed in PR bodies require exact-head CI verification before reuse.
- From prior PR #25: browser click may precede `send_clicked` telemetry; independently reproduce in the installed extension before claiming a live incident.
- Not observed: host runtime, active Edge ownership, installed extension files, ChatGPT API/browser state, live Telegram delivery, successful deployment.
- No production changes, retries, browser actions, token reads, PR merges or user message transmissions were performed by this document.
- Next reviewer must verify latest PR heads, select ONE canonical implementation per contract, and append immutable evidence with source versions before closing any gate.

## 12. References
- Chrome MV3 worker lifecycle: https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle/
- SQLite isolation: https://sqlite.org/isolation.html
- OpenTelemetry messaging semantic conventions: https://opentelemetry.io/docs/specs/semconv/messaging/
- Source repos: https://github.com/Podtverzhdeno/Tunnel and https://github.com/Podtverzhdeno/hermes-agent
