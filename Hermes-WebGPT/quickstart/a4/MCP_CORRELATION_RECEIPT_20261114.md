# A4 MCP guard correlation hardening — 2026-11-14

## Problem and fix

Previously `Guard.response` forwarded any backend JSON-RPC reply with an `id`, even if the agent never issued that request. A compromised or faulty backend could forge an agent-visible result. The guard also allowed duplicate concurrent request IDs, making tool-list filtering ambiguous.

`mcp_guard.py` now tracks every forwarded request ID, rejects duplicate IDs while outstanding, and drops unsolicited/replayed backend responses. For `tools/list`, it retains the existing tool filter; for permitted `tools/call` and initialize/ping, only the first correlated reply passes. Notifications are handled as before.

## Evidence

- SHA-256 `mcp_guard.py`: `dee349387a9bbcf72d3c9254b23ead6f11b5ba9d1dca9fefbfe6e3eed6884875`.
- SHA-256 `test_mcp_guard.py`: `ab7c2f1dffa8703763562cd8f31823d2ff22971410fb0d53011be967fb16f4e3`.
- Targeted guard + real subprocess stdio tests: **12/12 PASS**, rc 0, job `f27059e5e6ac456ab68ee84ddce71b64`.
- A4 suite: **188 tests, 180 PASS, 8 SKIP, 0 FAIL**, rc 0, job `78c92db0ba92442ab17b47d564dc4073`.
- Published to Draft PR #6, branch `hermes/a4-installer`: commits `30b8565f504df2e8f759113ad8c029ae77540fa2` and `bbc3503c6c1dfdfa80e7082eaa54f4d634794874`.
- Existing Windows processes, MCP grants, Tunnel profiles, secrets and runtime configuration were not changed.

## Limitations

This is a unit and subprocess transport security improvement, **not** independent A5 acceptance. It does not establish that the guarded endpoint is the only reachable MCP route, nor does it provide OS-enforced full-access containment. Windows clean-install, Yandex extension, and Telegram-to-WebGPT E2E remain outstanding.

## Next

Independently verify that every externally exposed MCP tool route terminates at the guard and that the unguarded 28-tool MCP cannot be reached by the agent; then validate full-access containment under a trusted broker and OS-enforced scope.
