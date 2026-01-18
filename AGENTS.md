## Agent Notes

- Telemetry admin endpoints live in `telemetry_service/routes/admin.py` and should
  remain protected by `get_current_admin_user`.
- When changing the telemetry admin experience, update the documentation in
  `docs/telemetry.md` and `docs/TELEMETRY.md`.

## Task Log

- 2026-01-18 20:10:18Z: Disambiguated range distribution SQL in `telemetry_service/app.py`, added JWT env keys in `.env`, dropped git stash, created `telemetry.service` alias and reloaded systemd, restarted `allinkeys-telemetry.service`. Tail logs show only 401 responses (no SQL errors); `caddyclient` not present in repo/requirements.
- 2026-01-18 20:24:23Z: Mounted `telemetry_dashboard/dist` at `/` in `telemetry_service/app.py` so the root UI serves, preserving API routes and static assets.
- 2026-01-18 20:34:06Z: Added `/api/machines` aliases, a single-machine detail endpoint, and a new machine control page (`telemetry_service/templates/machine.html`), plus a Control link in the machines dashboard.
- 2026-01-18 20:48:12Z: Logged telemetry control command application results in `core/telemetry.py` for pause/resume/set_mode/set_range handling.
- 2026-01-18 21:05:22Z: Allowed `/v1/dashboard` requests through API key middleware in `telemetry_service/app.py` to unblock dashboard UI.
- 2026-01-18 21:23:55Z: Added public `/v1/dashboard` API responses (health, recent ranges, contributors, machine series) with optional user scoping and updated telemetry docs.
