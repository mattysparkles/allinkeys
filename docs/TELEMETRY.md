# Telemetry

AllInKeys includes a small, privacy‑preserving telemetry system that helps the
maintainers understand how the software is used.  The data guides feature
development and is never shared or sold.

## Collected Fields

Only minimal metadata is transmitted and **never** any seeds, WIFs or derived
addresses.

| Field              | Description                                                  |
|--------------------|--------------------------------------------------------------|
| `app_instance_id`  | Random UUIDv4 stored on disk to distinguish installations    |
| `client_version`   | Version string from `config.settings`                         |
| `mode`             | One of `mnemonic`, `only_btc`, `puzzle`, `vanity`, `altcoin_derive` |
| `range_id`         | Optional range bucket identifier                              |
| `seed_fingerprint` | `SHA256(seed_bytes || app_instance_id)`                      |
| `timestamp_iso`    | ISO‑8601 timestamp of the event                               |
| `used`             | Whether the seed was previously seen                          |
| `match_found`      | Whether a funded address match was discovered                 |
| `machine_id`       | Stable, opaque machine identifier                             |
| `machine_name`     | Human-friendly display name                                   |
| `range_recent`     | Bounded list of recently checked ranges                        |
| `range_distribution` | Normalized metadata for plotting range density              |
| `reference_overlays` | Reserved for future reference range overlays                |

## Machine Identity & Naming

Telemetry uses a stable machine identifier derived in the following order:

1. OS machine identifier when available:
   - Linux: `/etc/machine-id` or `/var/lib/dbus/machine-id`
   - Windows: `MachineGuid` registry value
   - macOS: `IOPlatformUUID` (ioreg)
2. Fallback: hashed MAC address + hostname

The machine identifier is hashed into an opaque `machine_id` and persisted in
`logs/machine_identity.json`. The same file stores the generated display name.

### Naming Behavior

- If `MACHINE_NAME` is set in `config/settings.py`, that value is used as
  `machine_name`.
- Otherwise, a deterministic adjective‑noun name is generated from `machine_id`
  and stored in `logs/machine_identity.json`.

Changing `MACHINE_NAME` updates the display name only; historical telemetry is
still associated by the stable `machine_id`.

## Opt‑out

Telemetry is enabled by default.  To disable it, run the application with the
`--no-telemetry` CLI flag:

```bash
python main.py --no-telemetry
```

When disabled, no events are recorded and the queue remains empty.

## Central "Seen" Check

Before a seed is used, AllInKeys performs a quick, privacy‑preserving check
against the central telemetry service to avoid re‑using seeds that have already
been searched by any installation.

- Endpoint: `TELEMETRY_CHECK_ENDPOINT` (defaults to `${TELEMETRY_ENDPOINT}/check`)
- Request: `{ seed_fingerprint, mode, range_id }`
- Response: `{ "used": true|false }`
- Timeout: `TELEMETRY_CHECK_TIMEOUT` (default 1.5s). Network errors are treated
  as "unknown/not seen", so local work proceeds without blocking.

When a seed is skipped due to the central check, an event is queued with
`used=true` so the central service can account for the attempted reuse without
revealing the raw seed.

## Offline Behaviour

Events are written to a durable SQLite queue located under `logs/`.  If the
machine is offline, events accumulate and are uploaded the next time a network
connection is available.  The queue is capped at 100k entries and older records
are discarded in a ring‑buffer fashion.

## Admin Operations

The telemetry service includes admin-only endpoints under `/admin/*` for global
visibility. Admins can review user and machine summaries, aggregate keyspace
progress, and time-series metrics for KPS, backlog, and coverage. Access is
restricted to users with `is_admin = true` and requires a bearer token.

## Telemetry Dashboard API

The public dashboard UI consumes `/v1/dashboard/{slug}/*` endpoints. If a bearer
token is supplied, results are scoped to the authenticated user; otherwise data
is aggregated across all users.

- `GET /v1/dashboard/{slug}/machines`
- `GET /v1/dashboard/{slug}/machines/health`
- `GET /v1/dashboard/{slug}/ranges/recent`
- `GET /v1/dashboard/{slug}/ranges/distribution`
- `GET /v1/dashboard/{slug}/contributors/top`
