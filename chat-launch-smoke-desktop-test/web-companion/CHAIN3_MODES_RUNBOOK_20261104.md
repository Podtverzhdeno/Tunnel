# Chain3 selectable execution policy — 2026-11-04

## User-visible mode switch
From `chat-launch-smoke-desktop-test/web-companion` in the owner's normal terminal:

```powershell
python chain3_continuous.py mode
python chain3_continuous.py select-mode --mode sandbox --confirm-mode-selection
python chain3_continuous.py select-mode --mode trusted --confirm-mode-selection
python chain3_continuous.py preview
```

Selection is persisted to `bridge-state/chain3-mode.json` without editing the Chain3 manifest digest. Default for a fresh instance without a selection is `sandbox`. The change is allowed **only before Chain3 is armed or has its own jobs or handoff requests**. This prevents switching the meaning of in-flight work. Corrupt/linked selection files fail closed. Merely selecting `trusted` cannot create a job, ACK, signed owner grant, Windows identity or privileged capability.

## Policy
- **sandbox:** Existing Chain3 design: isolated role workspaces, bounded queue, deterministic successors, report SHA ACK and meaningful-work reviewer evidence. No Git merge, production deployment or elevated operations. This is *not* an infinite ChatGPT API allowance; it also still requires the original Chain2 completion and explicit Chain3 owner arm.
- **trusted:** Requested full host-trusted deployment. Until a separate OS-protected P085 broker and independent host attestation are connected, it is deliberately **blocked**. Claim, refill, arm and handoff completion approval never infer trust from an agent-editable settings file or P085 diagnostic. A user choice expresses **intent**, not cryptographic OS identity.

## Trust cutover — outstanding implementation and owner actions
1. Owner reviews the existing protected Windows broker release candidate, signed policy, scope and installer **outside agent-writable paths**. Never run elevated source from `IdeaProjects` without independent verification.
2. Owner provisions distinct broker/client Windows principals; protects code, Python interpreter, issuer, journal and endpoint with OS ACL; verifies signed policy and monotonic grants.
3. Independently verify identity over the real broker pipe (different SIDs, negative unauthorized calls, restart and stale/forged grant cases).
4. Wire an authenticated protected host attestation to `chain3_continuous.trusted_host_attested()` and prove it against a real protected service. The function currently returns False by design; **full trusted operation is not yet installed**.
5. Reconcile pending Chain2 report/ACKs with real browser evidence before allowing Chain3 admissions; do not replay UNKNOWN sends.
6. Owner authorizes the production cutover separately. No agent-created status file or `--confirm` switch substitutes for this.

## Current verified result
- Mode selector and enforcement implemented in `chain3_continuous.py`.
- Existing mode switch is explicitly non-authorizing. Post-selection `selected_mode=trusted`, `effective_mode=blocked`, `trusted_host_verified=false`.
- Focused Chain3 tests: job `ac61ca5d853046378f009222d72eebbf`, **22/22 PASS**, rc=0.
- Full web-companion Python suite: job `fc626d96a85845abbc49b73a0b60f341`, **194 tests, 193 PASS / 1 SKIP**, rc=0.
- No real browser successor created, no owner arm, no Windows broker service installed, no unknown job replayed. The OS-protected trust cutover is not completed.

## Boundaries
The selector is a UX and admission-policy choice. It is **not** the trusted owner approval root. Do not replace `trusted_host_attested` with `True`, trust a plain JSON/P085 diagnostic, or read a boolean from an agent-controlled file as authorization.

## Artifact-integrity hardening — 2026-11-04
Chain3's immutable request field `handoff_sha256` is now a versioned **bundle digest**, not simply SHA256 of `handoff.json`: it includes its exact bytes plus the name and SHA256 of **every** `changed_files` artifact (canonical sorted order). A post-request edit to any listed artifact invalidates `request_valid`, even if the optional `files_sha256` map was omitted. Each artifact is read in bounded chunks and limited to 16 MiB. This verifies local content integrity, **not** genuine test execution, Windows actor identity or owner approval.

Observed hermetic web-companion regression: job `cb844bc4defb435c91f80b2aea3dfdcd`, completed rc=0, **196 tests run, 195 passed, 1 skipped**. No Chain3 real job was created or ACK replayed.

## Path safety and streaming validation — further hardening on 2026-11-04
- Chain3 now rejects symlink/junction **directory segments**, including links that resolve back inside an allowed role workspace. The ancestry is checked before the handoff JSON is opened; this is a best-effort local guard, not an OS-enforced boundary against concurrent malicious modifications.
- The optional `files_sha256` checks and mandatory bundle binding share **one streaming digest pass**; the reader rejects oversized or concurrently growing artifacts rather than loading an unbounded file into memory.
- Duplicate `changed_files` entries are rejected.
- The role workspace may legitimately be absent before the first handoff: this preserves the existing `missing_or_unsafe_file` behavior.
- Scoped tests cover symlinks, junctions, duplicate entries, oversized files even with an optional hash, concurrent file growth and both valid/invalid optional hashes. Focused test receipt `f2c16f91c8f849b2ad4306da65f72b54`: **32/32 PASS**, rc=0.
- These checks cannot authorize P085 trust, Chain2 ACKs or Chain3 arm. Only an independently protected host can provide trusted evidence.
