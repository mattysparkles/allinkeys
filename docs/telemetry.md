# Telemetry Service Deployment Guide

## API keys

* Set `TELEMETRY_API_KEY` in `/opt/apps/allinkeys/telemetry_service/.env`.
* When the variable is set, **all** `/v1/*` endpoints require the `X-API-Key` header.
* When the variable is unset or empty, requests are allowed (useful for local development).

Example `.env`:

```bash
TELEMETRY_API_KEY=changeme
TELEMETRY_PORT=3088
```

## systemd service

1. Copy the service template:

   ```bash
   sudo cp /opt/apps/allinkeys/docs/telemetry.service /etc/systemd/system/telemetry.service
   ```

2. Copy your environment file:

   ```bash
   sudo cp /opt/apps/allinkeys/docs/.env.example /opt/apps/allinkeys/telemetry_service/.env
   sudo nano /opt/apps/allinkeys/telemetry_service/.env
   ```

3. Reload systemd and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now telemetry.service
   sudo systemctl status telemetry.service
   ```

## Test with curl

Replace the host and key as needed:

```bash
curl -H "X-API-Key: changeme" http://localhost:3088/v1/seed/stats
```

You can scope queries to recent activity using `since=5m|1h|24h` and optionally filter by mode:

```bash
curl -H "X-API-Key: changeme" "http://localhost:3088/v1/seed/stats?since=1h"
curl -H "X-API-Key: changeme" "http://localhost:3088/v1/seed/range?mode=btc_only&since=24h"
```

For POST ingestion:

```bash
curl -X POST http://localhost:3088/v1/seed \
  -H "Content-Type: application/json" \
  -H "X-API-Key: changeme" \
  -d '[{"seed_fingerprint":"abc123","used":true}]'
```

## Seed analytics endpoints

### `GET /v1/seed/stats`

Returns aggregate totals and per-mode counts.

Example response:

```json
{
  "total_seeds": 3024325,
  "unique_seed_count": 102342,
  "by_mode": {"btc_only": 23423, "vanity": 2030},
  "last_seen": "2026-01-18T20:43:00Z"
}
```

### `GET /v1/seed/range`

Returns range-level summaries with match counts and unique seed totals per range.

Example response:

```json
{
  "ranges": [
    {
      "range_id": "range-1",
      "count": 120,
      "match_found": 4,
      "unique_seed_count": 118
    }
  ],
  "since": "1h",
  "mode": "btc_only"
}
```

## Admin dashboard endpoints

Admin endpoints require a bearer token for a user with `is_admin = true`. All admin
routes live under `/admin/*` and can be used to build a global operations view.

### `GET /admin/dashboard`

Serves the HTML admin dashboard (includes aggregated cards, tables, and charts).

### `GET /admin/users/summary`

Returns per-user machine counts, average KPS, and coverage percentage.

### `GET /admin/machines/summary`

Returns the global machine list with owner info and current status.

### `GET /admin/keyspace/progress`

Returns aggregated keyspace totals, coverage, and time window metadata.

### `GET /admin/timeseries/kps`

Returns KPS history for plotting. Accepts `since=24h` and `bucket_minutes=15`.

### `GET /admin/timeseries/backlog`

Returns backlog counts (events with `used = 0`) over time.

### `GET /admin/timeseries/coverage`

Returns cumulative coverage percentages over time.

Example admin request:

```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:3088/admin/users/summary
```

## Security tips

* Deploy behind a TLS-terminating reverse proxy (nginx, Caddy, or Traefik).
* Restrict access to the telemetry service with firewall rules or private network rules.
* Rotate the API key periodically and store it in a secrets manager when possible.
* Monitor logs for repeated 401 responses to detect misuse.
