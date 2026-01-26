# Telemetry Service Deployment Guide

## API keys

* Set `TELEMETRY_API_KEY` in `/opt/apps/allinkeys/telemetry_service/.env`.
* When the variable is set, **all** `/v1/*` endpoints require the `X-API-Key` header.
* When the variable is unset or empty, requests are allowed (useful for local development).
* The AllInKeys client can send the header automatically when `TELEMETRY_API_KEY` is set.

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

## Client onboarding & pairing

The telemetry client supports an onboarding wizard that can pair a machine via
browser. Pairing endpoints:

* `POST /v1/pair/init` → `{pair_code, pair_url, poll_interval_seconds}`
* `GET /v1/pair/status?pair_code=ABC123` → `{status, token?}`
* `POST /v1/pair/claim` with `{pair_code, username, password}` to approve pairing

The pairing URL serves a small HTML page at `/pair` that lets a user approve a
pairing code. Tokens returned from pairing are secrets and should be treated
like passwords.

## Machine snapshots

When the client collects runtime and resource metrics it uploads a snapshot
for the registered machine. The endpoint returns the current
`MachineSummary` so dashboards stay in sync with the uploader.

* `POST /v1/machines/{machine_id}/snapshot`
  * Accepts the telemetry snapshot payload (see `telemetry_contract.py` for
    schema details).
  * The server also accepts `PUT` for backward compatibility; `POST` is preferred.
  * Requires a bearer token (and optionally `X-API-Key` in private deployments).

Example:

```bash
curl -X POST http://localhost:3088/v1/machines/<machine_id>/snapshot \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"identity": {...}, "runtime": {...}, "resources": {...}}'
```

## Range metadata visibility

Machine snapshots now persist the client-provided `range_recent` and
`range_distribution` blobs on the `machines` row, so the `/api/machines/*`
responses expose the parsed JSON. The dashboard UI and API consumers now prefer
the deterministic `machine_identity` (e.g. Frosted-Quartz) before falling back
to the opaque `machine_id`, making the friendly labels and range coverage data
available without silently dropping it.

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

### `GET /v1/seed/positions`

Returns recent submissions along with their estimated keyspace positions. The
normalized position (0–1) comes from the ranges recorded around the seed and is
used to render density charts in the dashboards.

Supports the usual filters (`mode`, `range_id`, `since`) plus `limit`
(default 20, max 500).

Fields:

| Field | Description |
| --- | --- |
| `seed_fingerprint` | Opaque identifier sent by the client |
| `range_id` | Range bucket covering the submission |
| `normalized_position` | Estimated location inside the keyspace |
| `machine_id` / `machine_name` | Source machine metadata |
| `used` / `match_found` | Flags sent by the uploader |
| `timestamp` | Last seen time for the seed |

### `GET /v1/seed/lookup`

Look up a fingerprint to highlight where it sits in the distribution and
retrieve the closest `n` seeds (default 5, max 50) with respect to normalized
positions. The endpoint accepts `seed_fingerprint`, `limit`, and `since`.

The response includes the requested seed plus a list of neighbors with
absolute Δ values (percent) so the dashboards can emphasize proximity on the
graph.

## Telemetry dashboard endpoints

Dashboard endpoints power the public telemetry UI. If a bearer token is
present, responses are scoped to the authenticated user; otherwise the results
aggregate across all users.

### `GET /v1/dashboard/{slug}/machines`

Returns machine activity buckets (series) plus any cached machine metadata.

### `GET /v1/dashboard/{slug}/machines/health`

Returns machine health status and stale markers. Supports
`stale_minutes=60`.

### `GET /v1/dashboard/{slug}/ranges/recent`

Returns the most recent ranges, default `limit=50`.

### `GET /v1/dashboard/{slug}/ranges/distribution`

Returns the range distribution snapshot, default `limit=200`.

### `GET /v1/dashboard/{slug}/contributors/top`

Returns the top contributors by submission count, default `limit=20`.

# Telemetry web UI

The public telemetry dashboard is served from [https://telemetry.sparkleserver.site](https://telemetry.sparkleserver.site) and consumes the `/v1/dashboard/{slug}/*` endpoints. Signing in via `/login` or pairing a machine stores a JWT in the `telemetry_token` cookie at the root path so every dashboard page loads your machines and runs automatically without requiring manual token pasting. Once authenticated you can:

* View machine-level detail and historical snapshots under `/dashboard/machine/{machine_id}`.
* Send control commands or global mode/range updates to selected machines.
* Explore the actual ranges reported by each run, visualize the cluster map with the normalized distribution view, and lookup specific ranges plus their nearest neighbors for quick analysis.

For administrators there is also `/admin/dashboard`, which stays protected by `get_current_admin_user` and surfaces the same aggregate telemetry metrics plus user/machine summaries.

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
