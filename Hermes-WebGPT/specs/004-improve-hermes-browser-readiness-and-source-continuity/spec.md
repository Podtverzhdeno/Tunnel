# Feature Specification: Hermes: готовность браузерного инструмента, сохранение источников между ходами и наблюдение задержек

**Status:** Draft — user review required

**Input problem:** На Windows Hermes выбирал POSIX npm shim вместо установленного native exe (WinError 193), затем не находил Chrome при наличии Edge. Публичные RSS-доказательства исчезали после первого tool turn, провоцируя лишний поиск. Существующий queue_monitor скрывает конкретные новые ошибки как other и не отделяет целевую скорость от утверждения о зависании.

**Target outcome:** Проверенный браузерный launcher без shell и загрузки пакетов; доступные публичные источники сохраняются на ограниченное время после инструментов без повторного fetch; существующий read-only монитор выдаёт точные безопасные причины и превышение целевого времени, не заявляя Telegram delivery по Relay done.

## User Scenarios & Testing

### User Story 1 — windows-browser-tool-readiness (P1)

Проверяемая готовность именно браузерного инструмента Hermes, отдельно от транспорта ChatGPT.

**Independent Test:**
Use isolated fixtures and execute the named acceptance scenarios.

**Acceptance Scenarios:**
1. Given Hermes resolves its local POSIX npm shim and the native package executable is present, when the project Gateway bootstrap starts, then the existing resolver uses one native executable argv item and records the actual Gateway PID and loaded helper hashes.
1. Given the resolver points outside the known package or the native PE/package identity is invalid, when readiness is evaluated, then no executable replacement or arbitrary installation occurs.
2. Given Chrome discovery fails and Edge is present in a known Windows installation location, when the browser tool is bootstrapped, then AGENT_BROWSER_EXECUTABLE_PATH points to that existing executable and a controlled test uses its own session.
2. Given AGENT_BROWSER_EXECUTABLE_PATH is explicitly set, when the compatibility helper runs, then the setting is preserved even if unavailable and no browser is silently installed.
3. Given the Gateway was restarted during a verified idle window, when activation is reported, then runtime PID and helper hashes match the running Gateway and Telegram connection state is checked.
3. Given only unit tests or a CLI version probe succeeded, when status is presented, then live browser navigation and Telegram delivery remain explicitly unconfirmed.


### User Story 2 — followup-source-continuity (P1)

Ограниченный cache уже найденных публичных доказательств для следующих model-only ходов.

**Independent Test:**
Use isolated fixtures and execute the named acceptance scenarios.

**Acceptance Scenarios:**
1. Given public evidence was obtained on the initial user turn, when the same task returns a tool result within the TTL, then the model-only view reuses a defensive copy with evidence age and cached-no-network attribution.
1. Given an entry expired, the monotonic clock moved backwards or the capacity was exceeded, when the cache is queried, then the stale or evicted entry is not reused and reads do not extend its lifetime.
2. Given a tool followup has no matching evidence, when the model view is built, then no network collection is triggered and no evidence is invented.
2. Given the original source collection returned no articles or timed out, when the evidence is reused, then the same limitation remains visible and no claim of a new successful search is added.
3. Given a newer or different user message exists, when the model view is built, then the previous request's evidence is not reused.
3. Given a caller mutates returned evidence, when a later model view is built, then the internal evidence cache and canonical request remain unchanged.


### User Story 3 — response-quality-observation (P1)

Доработка существующего queue_monitor вместо второго сервиса: типы ошибок и явные временные цели.

**Independent Test:**
Use isolated fixtures and execute the named acceptance scenarios.

**Acceptance Scenarios:**
1. Given a new request is blocked by content_tab_not_ready or tab_create_unverified, when it is compared with the previous snapshot, then the precise known failure is reported as new without exposing user data.
1. Given a reason contains arbitrary text, a URL or credentials, when a snapshot is emitted, then the value is replaced by other and never included verbatim.
2. Given an active request exceeds the configured target but not the stale threshold, when the monitor runs, then it reports a latency-target warning without declaring the worker dead or changing the request.
2. Given a request is terminal, when the monitor runs, then its creation age is not treated as current active latency.
3. Given done, blocked and uncertain requests started inside the selected window, when the report is built, then it reports counts and the inference completion ratio with its scope and time basis.
3. Given only queue metadata is available, when the report is built, then Telegram delivery, content correctness and p50/p95 phase latency remain unverified.


## Edge Cases
- Authorization denied, unavailable dependencies, repeated retries and concurrent modification.
- Invalid input, Windows Unicode handling and output/timeout limits.

## Functional Requirements
Normative requirements are owned by the OpenSpec delta files; this is an index.

| ID | Canonical OpenSpec requirement | Contract |
| --- | --- | --- |
| FR-001 | windows-browser-tool-readiness / Native launcher selection | The system SHALL replace only the known repository-local npm shim on Windows x64 with the verified installed native agent-browser PE executable, without invoking a shell or changing user arguments. |
| FR-002 | windows-browser-tool-readiness / Existing browser fallback | The system SHALL preserve an explicit browser executable setting and otherwise select an existing verified Chrome or Edge executable without touching a user profile. |
| FR-003 | windows-browser-tool-readiness / Truthful activation evidence | The system SHALL distinguish source installation, native CLI readiness, actual navigation, Gateway activation and Telegram delivery. |
| FR-004 | followup-source-continuity / Bounded public evidence cache | The system SHALL retain at most twelve public evidence entries for at most 300 monotonic seconds, keyed by the exact latest user request hash without storing raw private user text in the cache. |
| FR-005 | followup-source-continuity / No extra fetch or fabricated evidence | The system SHALL perform no new source fetch on a tool-only followup cache miss and SHALL preserve source failures and headline-only provenance. |
| FR-006 | followup-source-continuity / Canonical request preservation | The system SHALL keep canonical messages, real tool results, tool schemas and tool choice unchanged when adding model-only evidence. |
| FR-007 | response-quality-observation / Typed redacted failure categories | The existing read-only queue monitor SHALL preserve supported browser/bootstrap/draft failure codes while mapping untrusted unknown reasons to other. |
| FR-008 | response-quality-observation / Explicit latency target | The existing monitor SHALL optionally report an active request exceeding a configured response-age target separately from a stale-state alarm, using creation age rather than claiming phase duration. |
| FR-009 | response-quality-observation / Windowed inference outcome report | The existing monitor SHALL report recent inference outcomes using an explicitly defined request-creation window, with unknown ratios when no terminal observations exist and without equating done with Telegram ACK. |

## Success Criteria
- SC-001: All 9 mapped functional requirements have verified acceptance evidence.
- SC-002: No previously existing project files are overwritten by the specification generator.
- SC-003: Upstream OpenSpec strict validation passes for this change.
- SC-004: Implementation review explicitly distinguishes unit/mock from live integration results.
