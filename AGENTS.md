## Agent Notes

- Telemetry admin endpoints live in `telemetry_service/routes/admin.py` and should
  remain protected by `get_current_admin_user`.
- When changing the telemetry admin experience, update the documentation in
  `docs/telemetry.md` and `docs/TELEMETRY.md`.
