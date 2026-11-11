# Spec Delta

## Purpose
Проверяемая готовность именно браузерного инструмента Hermes, отдельно от транспорта ChatGPT.

## ADDED Requirements

### Requirement: Native launcher selection
The system SHALL replace only the known repository-local npm shim on Windows x64 with the verified installed native agent-browser PE executable, without invoking a shell or changing user arguments.

#### Scenario: Known Windows shim
- **GIVEN** Hermes resolves its local POSIX npm shim and the native package executable is present
- **WHEN** the project Gateway bootstrap starts
- **THEN** the existing resolver uses one native executable argv item and records the actual Gateway PID and loaded helper hashes

#### Scenario: Foreign or invalid launcher
- **GIVEN** the resolver points outside the known package or the native PE/package identity is invalid
- **WHEN** readiness is evaluated
- **THEN** no executable replacement or arbitrary installation occurs


### Requirement: Existing browser fallback
The system SHALL preserve an explicit browser executable setting and otherwise select an existing verified Chrome or Edge executable without touching a user profile.

#### Scenario: Edge installed
- **GIVEN** Chrome discovery fails and Edge is present in a known Windows installation location
- **WHEN** the browser tool is bootstrapped
- **THEN** AGENT_BROWSER_EXECUTABLE_PATH points to that existing executable and a controlled test uses its own session

#### Scenario: Explicit user choice
- **GIVEN** AGENT_BROWSER_EXECUTABLE_PATH is explicitly set
- **WHEN** the compatibility helper runs
- **THEN** the setting is preserved even if unavailable and no browser is silently installed


### Requirement: Truthful activation evidence
The system SHALL distinguish source installation, native CLI readiness, actual navigation, Gateway activation and Telegram delivery.

#### Scenario: Loaded build
- **GIVEN** the Gateway was restarted during a verified idle window
- **WHEN** activation is reported
- **THEN** runtime PID and helper hashes match the running Gateway and Telegram connection state is checked

#### Scenario: Offline-only evidence
- **GIVEN** only unit tests or a CLI version probe succeeded
- **WHEN** status is presented
- **THEN** live browser navigation and Telegram delivery remain explicitly unconfirmed
