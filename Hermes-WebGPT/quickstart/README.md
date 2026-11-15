# Hermes OneClick — Windows launcher candidate

**Development mode (temporary):** `Start-Hermes-All.cmd` now defaults to `-DevMode`, bypassing only the unfinished A4 production permission/acceptance gate. To restore strict startup use `Start-Hermes-STRICT.cmd` or set `HERMES_A4_STRICT=1` before launching. This does **not** disable Windows security, existing MCP/Tunnel authentication, secret protection or duplicate-launch checks. The existing 28-tool MCP keeps its actual privileges regardless of the A4 wizard's permission selection.

**Entry file:** `Start-Hermes-All.cmd` in the project root. Double-click it. No administrator PowerShell or manual MCP/Tunnel terminal is required **when tools are already installed/configured**. First-run clean clone still needs a verified Hermes runtime, Telegram bot token, MCP/Tunnel binaries and the user-installed Web Companion extension; they are intentionally not downloaded or silently installed from arbitrary sources.

## What is automated

1. Check `http://127.0.0.1:8765/healthz` for Local Workspace MCP; if stopped, launch installed `start-server.cmd` and await readiness.
2. Check `http://127.0.0.1:8080/readyz` for tunnel; if stopped, locate installed tunnel-client, load configured `local-workspace` profile and ask once for `CONTROL_PLANE_API_KEY` (hidden input).
3. Execute `tunnel-client doctor --profile local-workspace --explain` **before** saving the key or starting the tunnel. Save successful first-time key as user-scoped Windows DPAPI `Export-Clixml` under `%LOCALAPPDATA%\Hermes-OneClick\control-plane-key.dpapi.xml`. No `setx`, process command-line argument, repo file or normal output contains key.
4. Check Relay `:18765/health`; start using existing `start_bridge.py` only after verified PID/process ownership, no competing listener and read-only queue check. Stale launch receipt is backed up. `uncertain` states are never replayed.
5. Check Gateway PID, project command line and existing launch receipt; if no live instance, start existing `gateway_runner.py`. A process start is **not** a successful Telegram delivery E2E.
6. If every required stage passes, show summary. Repeated double-clicks reuse running components rather than spawn clones.

Output logs of managed processes are kept under `%LOCALAPPDATA%\Hermes-OneClick\logs`, not in the Git repository. The actual Gateway and Relay may retain their existing project-specific logs. Do not put private photos or tokens in logs.

## Paths

Supported source discovery on current Windows machine:
- MCP: `HERMES_MCP_START_CMD`, `vendor\local-workspace-mcp\windows\start-server.cmd`, or `%USERPROFILE%\Downloads\local-workspace-mcp\windows\start-server.cmd`.
- Tunnel: `HERMES_TUNNEL_EXE`, `vendor\tunnel-client\tunnel-client.exe`, `%USERPROFILE%\Downloads\tunnel-client-v0.0.19-windows-amd64\tunnel-client.exe`, or PATH.
- Tunnel profile: `%APPDATA%\tunnel-client\local-workspace.yaml`.
- Python: installed Hermes venv, `.venv\Scripts\python.exe` or PATH.
- Telegram Gateway: existing `profile\.env` and `gateway_runner.py`.
- ChatGPT Web: owner must sign in to an existing browser profile and explicitly load Web Companion extension once. Script never extracts cookies or bypasses browser install approvals.

## Safe tests and recovery

Read-only preview with no state mutation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\quickstart\start.ps1 -DryRun
```

To retry after replacing a tunnel control-plane key, execute the launcher with `-ResetKey`; this deletes only the DPAPI copy and requests a new secure input. Your API key is never printed.

Automated regression:

```powershell
python -m unittest discover -s quickstart -p "test_*.py" -t . -v
python -m unittest discover -s tests_media_bridge -p "test_*.py" -t . -q
```

**Do not enable live media:** `HERMES_MEDIA_ENABLED` stays unset. The new owned-download connector is still an experimental adapter. A live, signed-in ChatGPT Web canary with real Telegram↔Web binary traffic and verified 3-lane isolation is required before production activation.

## Проверено на установленном Windows-ПК (13.11.2026)

- `python quickstart/verify_idempotent_running.py`: **PASS** — перед настоящим (не DryRun) вызовом `start.ps1` подтверждены все четыре компонента; после запуска контрольные суммы `bridge_process.json` и `gateway_launch.json`, идентичность Gateway и Relay неизменны, новых промптов не отправлено.
- `python quickstart/inspect_dryrun_readonly.py`: MCP=True, Tunnel=True, Relay=True, Gateway=already_running. Исправлен реальный формат MCP `/healthz`: `{status: "ok", tools: 28}` вместо ошибочно ожидаемого `ok:true`.
- `python -m unittest discover -s quickstart -p "test_*.py" -t . -v`: **19/19 PASS**. Проверки выполняются без остановки работающих процессов.
- Это доказывает **идемпотентный запуск при уже установленных/работающих компонентах**. Не доказывает чистую установку, успешную загрузку файлов в ChatGPT Web или Telegram E2E после перезапуска.

## Remaining work for literal "git clone → click → key"

Bundle or acquire verified upstream installers with pinned versions and hashes, include a first-run Telegram onboarding wizard, verify browser extension manifest and one-time loading, then test on a completely clean Windows 10/11 VM. Until those steps are complete, this is **one-click restart for preconfigured systems**, not proof of zero-prerequisite installation.
