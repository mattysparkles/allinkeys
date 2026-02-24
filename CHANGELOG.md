# Changelog

## [Unreleased]

## [v0.1.2]

- Fixed telemetry seed client initialization (`_CLIENT` scope) so seed/range events queue and flush correctly again.
- Added BTC-only pre-run range telemetry emission fallback for accurate recent range and total submission metrics.
- Added dedupe-aware machine registration using stable `machine_identity` to prevent duplicate machine rows after telemetry setup/repair.
- Hardened snapshot ingest updates to avoid empty range payloads overwriting existing machine range telemetry state.
- Added `tools/telemetry_diag.py` for one-command telemetry diagnostics on client machines.

## [v0.1.1]

- Added privacy-safe central telemetry with durable seed queue
- Enforced puzzle mode range validation and hardened seed tracker
- Added rolling metrics and mode-aware GUI for real-time insights
- Added `env_path` helper and migrated many modules to `pathlib`-based paths
- Introduced `--purge` command with dry-run for cleaning old downloads
- Added opt-in telemetry module and consent logging with alert redaction
- Added Docker support and compose configuration
- Implemented dashboard authentication and premium licensing module
- Added plugin entry point system and templates
- Improved GPU detection, selection, and scheduler tests
- Enforced HTTPS downloads with checksum verification
- Added processing throughput metrics and SQLite fallback for funded address lookup
- Stream VanitySearch output to track seeds and expanded binary detection
- Enhanced mnemonic mode with full BIP-39 language support and multilingual output
