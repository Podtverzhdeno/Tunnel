# Tasks: Preserve Web Companion recovery blockers

- [x] Inspect original Bridge code and read-only recovery decision.
- [x] Identify event-tail truncation and damaged-history overwrite risk.
- [x] Implement persistent first-blocker retention in same metadata JSON.
- [x] Expand blocker set for existing tab/account identity warnings.
- [x] Reject invalid existing history without erasing its bytes.
- [x] Add focused Python bridge server regression cases.
- [x] Write full causal observability SDD and conflict-ownership plan.
- [ ] Independently run full Python test_bridge.py and offline suite on Windows/Linux.
- [ ] Verify GitHub Actions on exact PR head SHA.
- [ ] Confirm source-vs-installed extension v0.6.6/v0.9.7 mismatch.
- [ ] Run owner-approved runtime continuity test without a new Send.
- [ ] Review PR #10/#11 worker changes and PR #13 triage integration.
- [ ] Independent security review and explicit deployment approval when required.

Completion of checked source-edit tasks does NOT mean the runtime/CI/release
gates are passed. No automatically retried user job is allowed.
