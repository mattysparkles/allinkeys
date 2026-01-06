# Codex CLI Prompt: Telemetry Health Check for AllInKeys

Use the following prompt with Codex CLI when working on the production host for `telemetry.sparkleserver.site`. It is tailored to the current AllInKeys layout and should be run from the repository root (`/workspace/allinkeys`) while invoking `python main.py` in any supported mode (e.g., mnemonic, only BTC, puzzle, vanity, altcoin derive).

---

You are operating on a live production Linux server that hosts multiple projects.

Project: allinkeys
Domain: telemetry.sparkleserver.site
Repository root: /workspace/allinkeys
Primary entrypoint: python main.py [flags]

GOALS
1. Whenever `python main.py` is launched (any mode such as `--mnemonic`, `--only btc`, `--puzzle`, vanity defaults, or altcoin derive), verify telemetry is on by default (skip the `--no-telemetry` flag) and add a lightweight self-check that:
   - Confirms the telemetry process from `core.telemetry.start_embedded_telemetry_service()` is running locally.
   - Confirms it can accept incoming telemetry events queued by the run (check `logs/` SQLite queue if offline).
   - Logs success/failure to the existing logger without interrupting key generation or other workflows.

2. Create or update a small backend dashboard for telemetry under `telemetry_service/` that:
   - Exposes **read-only** metrics (counts of contributing machines over time, recent keyspace ranges submitted, top contributors by range submissions, explored-range distribution, last heartbeat per machine and stale nodes).
   - Runs on an unused localhost port and is safe to restart without affecting `python main.py` or other services.
   - Provides simple JSON endpoints to back the dashboard and may use obscurity-by-path instead of heavy auth.

DASHBOARD METRICS (minimum viable)
- Number of contributing machines over time (support hourly/daily rollups if feasible).
- Latest keyspace ranges submitted (stream or recent window).
- Top contributing machines by submitted ranges.
- Distribution of explored ranges vs total keyspace.
- Basic system health: last heartbeat per machine, stale node detection.

DEPLOYMENT CONSTRAINTS
- Bind only to an unused local port and reverse-proxy via the existing Caddy setup.
- Do **not** change existing Caddy routes; add only the minimal scoped route for telemetry.sparkleserver.site after reviewing the Caddyfile.
- Use isolated dependencies (virtualenv if needed); avoid global upgrades.
- Avoid destructive database actions and keep the current telemetry ingestion format intact (only additive changes allowed).

SERVER SAFETY
- Do not affect other projects or domains on the host.
- Keep key generation and alerting flows running; telemetry checks must be non-blocking.
- No try/except around imports; follow repository coding style.

DOCUMENTATION
- Read any existing `AGENTS.md` (create one in the repo root if missing) and update it after changes to describe:
  - What the telemetry service does
  - How the dashboard works and its ports
  - Safe redeploy steps
  - How to extend metrics later

OUTPUT EXPECTATIONS
- List exact files created or modified and show Caddyfile additions separately.
- Include inline comments explaining each major component you add.
- Favor clarity and safety over cleverness; proceed step by step and avoid unstated assumptions.

OPERATIONS CHECKLIST
- Start from `/workspace/allinkeys` and run `python main.py` with representative flags for each mode you touch (mnemonic, BTC-only/puzzle, vanity, altcoin derive) to confirm telemetry events are emitted.
- Verify dashboard endpoints respond locally and through the new Caddy route.
- Keep logs in `logs/` and avoid noisy output that could disrupt long-running sessions.

---
