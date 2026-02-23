## Agent Notes

- Telemetry admin endpoints live in `telemetry_service/routes/admin.py` and should
  remain protected by `get_current_admin_user`.
- When changing the telemetry admin experience, update the documentation in
  `docs/telemetry.md` and `docs/TELEMETRY.md`.

## Task Log

- 2026-02-23 20:04:48Z: Started investigation into global telemetry dashboard metrics (total submissions/recent ranges) not updating for live machine `frosted-quartz`.
- 2026-02-23 20:22:11Z: Diagnosed telemetry ingest stalling after 2026-02-21 (no `/v1/machines/*/telemetry` since then; `seed_events` last_seen 2026-02-21) while snapshots/control/checks continue; updated `telemetry_service/routes/machines.py` to prevent empty snapshot range payloads from overwriting existing range data; attempted `pytest tests/test_telemetry.py -q` (failed: multiprocessing semaphore permission error); restarted `allinkeys-telemetry.service`.
- 2026-02-23 21:49:41Z: Investigating duplicate `Frosted-Quartz` machine entries after running `--telemetry-setup` (new machine registrations leaving old rows).
- 2026-02-23 21:57:06Z: Added dedupe-aware machine registration: `MachineRegisterRequest` now accepts `machine_id`/`machine_identity`; server reuses existing machine by identity or id and updates metadata; snapshot ingest now updates `machine_identity`; telemetry setup sends persisted `machine_id` + stable `machine_identity`; TelemetryClient registration includes `machine_identity`. Restarted `allinkeys-telemetry.service`.
- 2026-02-23 21:59:50Z: Deleted duplicate `Frosted-Quartz` machine rows (`a12f4279-1312-4179-a83c-dcdd8ed1fccd`, `bc9f791b-107e-44fa-a54e-9bd44945d696`) and kept newest `29e3dad7-f86b-49f2-8ee2-b69c41e6a7b5`; restarted `allinkeys-telemetry.service` to clear in-memory registry.
- 2026-02-23 22:02:30Z: Investigating continued lack of recent range submissions/total submissions updates after client update in btc-only mode.
- 2026-02-23 22:15:39Z: Verified telemetry service still has no `/v1/machines/*/telemetry` ingest since 2026-02-21; `seed_events` max last_seen remains 2026-02-21T19:06:57Z.
- 2026-01-18 20:10:18Z: Disambiguated range distribution SQL in `telemetry_service/app.py`, added JWT env keys in `.env`, dropped git stash, created `telemetry.service` alias and reloaded systemd, restarted `allinkeys-telemetry.service`. Tail logs show only 401 responses (no SQL errors); `caddyclient` not present in repo/requirements.
- 2026-01-18 20:24:23Z: Mounted `telemetry_dashboard/dist` at `/` in `telemetry_service/app.py` so the root UI serves, preserving API routes and static assets.
- 2026-01-18 20:34:06Z: Added `/api/machines` aliases, a single-machine detail endpoint, and a new machine control page (`telemetry_service/templates/machine.html`), plus a Control link in the machines dashboard.
- 2026-01-18 20:48:12Z: Logged telemetry control command application results in `core/telemetry.py` for pause/resume/set_mode/set_range handling.
- 2026-01-18 21:05:22Z: Allowed `/v1/dashboard` requests through API key middleware in `telemetry_service/app.py` to unblock dashboard UI.
- 2026-01-18 21:23:55Z: Added public `/v1/dashboard` API responses (health, recent ranges, contributors, machine series) with optional user scoping and updated telemetry docs.
- 2026-01-30 06:53:48Z: Updated public dashboard endpoints to prefer human-readable machine names in health/recent ranges/top contributors, documented the label changes, and restarted `allinkeys-telemetry.service` to make the updates live.
- 2026-02-10 19:30:00Z: Expanded dashboard filtering and metrics: added `scope`/`machine_id`/`range_id`/`until` filters plus new `/v1/dashboard/{slug}/ranges/ids` and `/v1/dashboard/{slug}/metrics/aggregate` endpoints, enabled cookie-scoped dashboard access, added BTC address-type counters in dashboard metrics, and refreshed the public telemetry UI with full-width charts, zoom controls, filter UI, and address-checked charts. Linked machine cards to the public telemetry explorer and updated telemetry docs.
- 2026-02-10 22:32:09Z: Committed/pushed telemetry dashboard updates to GitHub and restarted `allinkeys-telemetry.service` to make the endpoints and UI changes live.
- 2026-02-10 22:50:31Z: Tightened telemetry dashboard chart zoom limits to the 0–100% keyspace range with dynamic y-bounds, added 401 fallback for aggregate metrics, and refreshed the public dashboard observer script.
- 2026-02-10 23:40:30Z: Added SQLite busy timeout/connection timeout in telemetry DB connections to reduce "database is locked" 500s affecting dashboard charts.
- 2026-02-10 23:44:06Z: Moved telemetry DB schema setup behind a one-time init lock to avoid per-request DDL and reduce "database is locked" errors; pushed to GitHub and restarted `allinkeys-telemetry.service`.
- 2026-02-11 00:22:17Z: Added seed-queue tables/endpoints and `queue_seed` control handling (client-side queue consumption in keygen), plus telemetry dashboard crosshair selection, scrollbars, and queue UI updates; refreshed telemetry docs.
- 2026-02-11 00:50:12Z: Moved chart Y-scrollbars onto the y-axis, enabled crosshair + click-to-queue on position charts, and added since/until support to aggregate metrics; updated telemetry docs.
- 2026-02-11 02:39:39Z: Added persistent marker dots + counter/clear button for chart clicks, crosshair on metric charts, and y-axis scrollbar positioning tweaks in telemetry dashboard UI.
- 2026-02-11 03:02:37Z: Fixed crosshair alignment, added purple marker overlays across position charts, and added seed queue progress polling via machine metrics; client now tracks `seed_queue_depth` and logs queued seed consumption.
- 2026-02-11 03:36:20Z: Improved crosshair cursor tracking, unpinned crosshair after clicks, expanded marker overlays across charts, and enhanced queue push error reporting in the telemetry dashboard UI.
- 2026-02-11 05:05:41Z: Fixed seed queue push validation by using `get_machine_for_user` in queue endpoints to resolve a NameError causing 500s.
- 2026-02-11 05:10:39Z: Pushed the queue validation fix to GitHub and restarted `allinkeys-telemetry.service`.
- 2026-02-11 05:20:12Z: Bumped Windows release workflow default tag to v0.1.1 and updated changelogs/readme for v0.1.1 release prep.
- 2026-02-11 05:25:46Z: Tagged and pushed v0.1.1 to trigger the Windows release workflow.
- 2026-02-11 06:19:31Z: Routed telemetry payload debug output through logger (DEBUG only) to stop massive stdout dumps during flushes.
- 2026-02-11 08:34:31Z: Addressed CodeQL alerts by hardening redirect sanitization, removing regex-based relative parsing, parameterizing dashboard queries, adding log redaction, and declaring workflow permissions.
