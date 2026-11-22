# SDD-HERMES-TUNNEL-002 — Read-only diagnosis of existing Web Companion v0.6.6

**Status:** Draft; **source-tested diagnostic prototype**, NOT deployed, not installed as a browser extension. 2026-11-22.

**Exact target project:** `Podtverzhdeno/Tunnel`, `chat-launch-smoke-desktop-test/web-companion/` and the *unpacked extension* source under `chat-launch-smoke-desktop-test/web-extension/`. Historical base branch SHA `350ea33080bc35245aa6741f85fa926cc8a8fcaf`. The companion is a loopback bridge on `127.0.0.1:18763`, not the same as Local Workspace observer MCP `127.0.0.1:8765` or scoped editor at `8766`. Keep them separate.

**Related implementation:** `Podtverzhdeno/hermes-agent` PR #8, draft SDD-HE-GPT-001 and an independently tested event inbox, trace doctor and operator report. The fork's Autopilot branch (separate ownership) must remain the task/effect authority; the Tunnel bridge and Hermes event inbox are *observational* sources, not competing schedulers.

## 1. Evidence and operating constraints

Source audit (reviewed actual GitHub files in Tunnel main): `web-companion/README.md`, `manifest.json`, `status.py`, `bridge.py`, `background.js`, `chain2_lineage_monitor.py`, `chain2_blocker_dossier.py`, `chain3_continuous.py`, `p085_readiness_matrix.py`, `test_status.py`. The installed browser profile, Windows services and current bridge-state **could not be inspected**: the connected local MCP was unavailable. Historical reports of a 0.6.5 worker while 0.6.6 was on disk are *not* evidence of current status.

**Do not** delete/reinstall a running browser extension, clear `chrome.storage`, close unidentified tabs, restart a busy broker or remove `admission-hold.json` to make metrics green. Version changes require the existing operator-controlled reload/rollout protocol. An `account_context_changed`, sent-but-unconfirmed or ambiguous effect requires reconciliation and possibly manual intervention; no automatic retry of old job IDs.

Web Companion **already** has a finite Chain2 with developer/reviewer dependencies and report ACK, plus experimental Chain3 gated behind owner approval and a P085 protected-host attestation that is absent in the current audited source. Do not bypass or duplicate these mechanisms. Its browser polling allows at most 25 active tabs and 4 new claims per cycle. "Dozens of agents" means bounded waves of work, not unlimited concurrent ChatGPT tabs. One user tab or MV3 worker restart is not a durable scheduler.

## 2. Stop-cause model: do not confuse observations and truth

`hermes_readonly_doctor.py` is a self-contained **local, read-only** snapshot of existing `bridge-state` files. It never issues a network request or browser command, reads the bearer token, opens a tab, mutates queue/hold, or creates a report/ACK. It returns only enumerated metadata, sanitized UUIDs, bounded counts, per-job last five allowlisted stages and hypotheses. The report includes `atomic_across_files=false` and `browser_or_provider_outcome_trusted=false`. A one-time multi-file read cannot prove a single atomic global instant; concurrent rename/move can look like a duplicate.

| Signal | Observation | Competing hypotheses | Fail-closed action |
|---|---|---|---|
| `ADMISSION_HOLD_PRESENT` | admission-hold file exists | deliberately paused rollout, unsettled send, account dispute | respect hold; investigate owner-approved release procedure |
| `EXTENSION_VERSION_MISMATCH` | version differs from bridge-required 0.6.6 | stale browser extension profile, rollout incomplete | no new sends; operator reload through existing procedure |
| `EXTENSION_HEARTBEAT_STALE` | last metadata timestamp beyond threshold | sleeping MV3 worker, bridge outage, stopped browser, delayed disk writes | inspect bridge health/worker independently; no inferred success or failure |
| `EXTENSION_NOT_POLLING` | last reported worker state is not `polling` | explicit pause, circuit breaker, authorization problem | look up history and hold; do not click resume automatically |
| `NO_BROWSER_WINDOWS` | reported window count = 0 | browser closed, login inaccessible, stale heartbeat | operator browser check, no background tab creation |
| `AMBIGUOUS_EXTERNAL_OUTCOME` | historical unknown, watchdog, tab identity dispute | send may have happened; receipt or account changed | **never resend existing job**; reconcile durable bridge ACK/receipt |
| `POSSIBLY_SENT_AWAITING_RECEIPT` | sent/saved signal on active job without confirmation | model still working, lost ACK, browser closed | compare authoritative receipt, no speculative retry |
| `DONE_REPORT_ACK_UNVERIFIED` | report-mode done record without readable report ACK marker | file race, missing/corrupt ACK, incomplete report | independently verify SHA, ACK, exact job binding |
| `ACTIVE_JOB_TELEMETRY_SILENCE` | no recent active-job event | long model run, stale bridge, lost event | inspect channels, heartbeat and queue, preserve job ID |
| `STATE_DUPLICATE_OR_TRANSITION` | same UUID visible in >1 state folder | legitimate concurrent atomic move, collision | take another snapshot; never delete or requeue based on one read |
| `STATE_SCAN_TRUNCATED` | >500 JSON names in one state directory | huge/attacker-controlled backlog | mark incomplete; operator uses full dedicated ledger tools |
| `JOB_TELEMETRY_UNAVAILABLE` | missing, untrusted or oversized diagnostics | valid empty state, partial writer, permissions, corruption | unknown cause; do not read private data or infer stopped run |

A displayed "read-only scan OK" means the scanner executed without **critical diagnostics** in its finite evidence subset. It never proves no stalled jobs, business success, provider response, consent or complete queue coverage. Every finding carries severity and epistemic certainty; source metadata is untrusted until authenticated by the actual owner/collector.

## 3. Security and resource-control invariants

- The scanner reads only `extension.json`, queue/active/done job records, bounded per-job diagnostics, ACK file metadata. The admission-hold body is *never* opened; the `token` file is *never* read. No raw prompts, URLs, transcripts, cookies, account identity or HTTP Authorization headers in output.
- File reads are capped at 64 KiB per job and 4 KiB per extension heartbeat. Directory scans cap at 500 entries per state; telemetry arrays at 500. Invalid JSON or duplicate keys are rejected without reflecting data. Symlinks/junctions in state paths are rejected; where available `O_NOFOLLOW` and before/fd/after stat consistency checks reduce races. These are **not** Windows OS-backed attestation.
- Time from local telemetry is not a signed provider time. For independent sources, wall clocks cannot establish causality; future bridge integration must assign monotonically increasing offsets at authenticated ingest.
- User-originated content, event reason and file names are allowlisted; unknown fields are dropped, not printed. `bridge-state` is agent-writable in many deployments, so diagnostics cannot grant trusted actions or policy.
- The report never invokes `chain3_continuous.arm`, `role_campaign.arm`, API `/v1/claim`, browser send, owner approval, or protected-host operations.
- A valid `REPORT_ACKED` label alone is not enough to prove report contents. Reuse existing `receipt_closure_candidate()` and independent SHA readback in a trusted integration boundary; do not call report `done` without them.

## 4. Multi-agent integration contract and rollout

Source authority mapping: Tunnel Web Companion is authoritative only for its own persisted queue/receipt/ACK state; Hermes Autopilot is authoritative for task ownership, fencing, permissions and effects; the GPT event inbox stores *authenticated observation receipts*, **not** trusted provider outcomes; an independent verifier owns exact-revision evidence. Avoid cross-DB transactional/exactly-once claims: the systems have separate SQLite/filesystem transactions.

Recommended adapter contract (not implemented): read each source in a bounded local collector under owner authorization; translate allowlisted events to Hermes schema with stable `run_id`, `task_id`, `project_id`, `attempt_id`, `operation_id`, `emitter_id`; assign collector offset and UTC; authenticate with an out-of-band key; redact all user content; reconcile provider receipts before allowing new tasks. Ensure `job_id` is a stable operation identifier across restarts; local source IDs must not be confused with verified signed identities.

**Release phases**

- P0 (implemented): standalone read-only global stop doctor + isolated synthetic unit tests; keep ordinary `status.py` and Chain2 blocker monitors intact.
- P1 (not implemented): owner-run local collection on existing state, protected credentials, system health and heartbeat checks, sampling while admissions remain held, no browser automation.
- P2 (not implemented): authenticated metadata event bridge into Hermes PR #8 inbox; exact-once *ingest* only, no exact-once provider actions; integrate with Autopilot ownership/fencing after its merge and review.
- P3 (not implemented): fault injection (simulated MV3 suspension, bridge 429, vanished ACK, a tab already sent, pre-send UI bug), version/rollout canary and explicit operator rollback.
- P4 (not implemented): measured synthetic 10/25/50/100 worker orchestration with aggregate budgets, fairness, no duplicate business effects, health of mounted local MCP (8765) and bridge (18763) measured independently. Never increase actual tab cap as a shortcut.

**Local command** from `chat-launch-smoke-desktop-test/web-companion`:

```bash
python hermes_readonly_doctor.py
python -m unittest -v test_hermes_readonly_doctor.py
```

Exit code `0`: no critical finding among safely inspected metadata; `1`: critical incident; `2`: invalid source or permission boundary. This is an explicit one-shot operator action, **not** a permanently running monitor or automatic repair. No browser login, bridge token or host permissions required for tests.

## 5. Idea prioritization (subjective, reproducible)

Formula: score/10 = 2 × (0.30 impact + 0.25 reliability + 0.20 feasibility + 0.15 confidence in external evidence + 0.10 risk reduction), each criterion 1–5. Scores are proposals, not measurements, and must be revised after real incident data.

| Idea | I | R | F | E | Risk | Score | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| Read-only stop attribution without side effects | 5 | 5 | 5 | 5 | 5 | 10.0 | Build P0 |
| Reuse existing ACK and immutable job IDs | 5 | 5 | 4 | 5 | 5 | 9.6 | Integrate later |
| Prevent unsafe resend after ambiguous send | 5 | 5 | 4 | 5 | 5 | 9.6 | Hard safety gate |
| Authenticated event bridge with provenance | 5 | 5 | 3 | 5 | 5 | 9.2 | P2 |
| Independent source heartbeat and version gates | 5 | 4 | 5 | 4 | 5 | 9.2 | P0/P1 |
| Fair multi-agent wave scheduling | 5 | 4 | 3 | 4 | 5 | 8.4 | after load tests |
| Long-horizon anomaly model on chat text | 2 | 2 | 2 | 1 | 2 | 3.7 | Reject now |

## 6. QA / traceability

At initial commit: 22 isolated offline Python unit cases on synthetic fixtures, with privacy tests, stale heartbeat/version/hold checks, account/unknown send, pre-send ambiguity, duplicate state transition, filesystem symlinks/duplicate JSON keys, bounded reads/scan and CLI exit/readonly semantics. **These tests are not a live browser acceptance run.**

Ticket/incident log fields for every subsequent change: bridge job UUID, campaign/task ID (if known), timestamp/source/collector offset, source SHA, observed facts, alternative causes, severity/certainty, receipt/ACK match, hold and permission state, proposed minimal fix, regression command, reviewer and rollback. If source is unavailable, record `UNKNOWN`, never fabricate a cause.

**Additional owner constraints carried from the project:** do not use `nohup`/`disown` to paper over unreliable tunnels. For MCP reconnect, `runtimes connect` alone is not enough: verify `process_running=true`, `healthy=true`, `ready=true` with runtime status. Duplicated Tunnel client routes may cause auth faults; this read-only scanner does not terminate or replace them.

**P0 implementation correction log:** first ranking table calculation for the low-priority anomaly model was corrected from 3.6 to 3.7 under the published formula. Added a bounded redacted per-job recent-event timeline; do not interpret extension-provided timestamps as authoritative causality.
