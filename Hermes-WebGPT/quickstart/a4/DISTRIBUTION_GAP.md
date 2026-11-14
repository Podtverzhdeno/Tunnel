# Hermes A4 — clean-clone distribution gap (2026-11-14)

**Gate: BLOCKED.** A current Windows developer checkout is NOT the same as a clean clone of `Podtverzhdeno/Tunnel`.

## Verified facts

- Local source auditor: `quickstart/a4/dependency_closure.py`, job `bd3673327a9844e4988258ef941d2c5d`, exit 0.
- Local Python import closure for `relay.py`, `gateway_runner.py`, `start_bridge.py`: **24 required Python source files**. This is a static root-import closure, not exhaustive dynamic-dependency enumeration.
- Local source heuristic found **0 common literal credential patterns** in that closure. This does NOT prove it contains no private data or credentials; a human security review must precede any public publication.
- GitHub Draft PR `hermes/a4-installer` currently has `Hermes-WebGPT/relay.py`, but direct lookup for `Hermes-WebGPT/gateway_runner.py` and `Hermes-WebGPT/start_bridge.py` failed. Do not claim the clone is independently runnable.
- A4 `preflight.py` now checks the 24-file source presence contract, reports the missing module names and blocks a readiness claim if any are absent.

## Required before clean-clone acceptance

1. Obtain owner-reviewed, privacy-reviewed, license-compatible distributable copies of the 24 required Python modules, along with all dynamically loaded modules and static assets.
2. Package compatible MCP host, tunnel client, Relay, Gateway, browser extension and pinned Hermes Agent core. Never vendor user config, browser profiles, session state, allowlists with private IDs, logs or API keys.
3. Verify hashes and legal provenance, then run an actual Windows 10/11 clean VM installation through the double-click wizard.
4. Verify both permission modes at the real MCP boundary, not only in the GUI; test Telegram → Yandex ChatGPT Web → Telegram with text, files and images.
5. Perform independent A5 production acceptance before merging Draft PR #6.

The launcher intentionally fails closed when critical components or permission enforcement cannot be verified. **No fake completeness from a developer-machine health check.**
