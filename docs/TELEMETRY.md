# Telemetry

AllInKeys includes a small, privacy‑preserving telemetry system that helps the
maintainers understand how the software is used.  The data guides feature
development and is never shared or sold.

## Quick Start: telemetry setup

On first run (or when the token is missing/invalid), AllInKeys will guide you
through a quick setup wizard in an interactive terminal. You can also run it
manually:

```bash
python main.py --telemetry-setup
```

The wizard will:

1. Show a concise privacy disclosure (what is sent and what is not).
2. Let you paste an existing token, pair via browser, or disable telemetry.
3. Store your token locally so future runs “just work.”

Public pairing requires no API key. API keys are intended for private/on-prem
telemetry deployments only. The public SparkleServer telemetry service does not
require `TELEMETRY_API_KEY` for pairing or telemetry. If your private telemetry
service requires an API key, set `TELEMETRY_API_KEY` in the environment so the
client can include the `X-API-Key` header during pairing and telemetry requests.

## Token storage (local-only)

Telemetry tokens are stored locally at:

```
config/.telemetry_token
```

This file is git‑ignored. Tokens are secrets—do not share them. Rotate tokens
immediately if they are exposed.

Telemetry opt‑out is stored locally at:

```
config/local_telemetry.json
```

## Account signup & login

The telemetry dashboard supports multiple users. You can create an account at
`/signup` and sign in at `/login`. After signing in, the browser stores a
session cookie so your dashboard and pairing approvals stay authenticated
across page loads.

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

You can also disable telemetry during setup, which writes a local opt‑out flag
to `config/local_telemetry.json`.

## Pairing flow (browser)

If you choose “Pair this machine via browser” in the wizard:

1. The client requests a pairing code from `/v1/pair/init`.
2. You open the pairing URL. If you are not signed in, you are redirected to
   `/login` or `/signup`.
3. After authentication, approve the pairing code at `/pair`.
4. The machine polls `/v1/pair/status` until approved and receives a token.
5. The token is stored locally and the machine is registered automatically.

## User login and signup

The telemetry UI exposes simple authentication pages:

- `/signup` creates a user and immediately starts a session.
- `/login` signs in with existing credentials.
- `/logout` clears the session.

Use the `next` query parameter to return to the original page after
authentication (pairing uses `next=/pair` automatically).

## Dashboard access

The authenticated dashboard UI lives at:

- `/dashboard/machines` for the machine list
- `/dashboard/machine/{machine_id}` for details and control

## Token lifecycle

- Telemetry access tokens are short-lived JWTs issued when you log in, sign up,
  or approve a pairing request.
- Tokens are stored locally in `config/.telemetry_token` and expire after the
  configured `TOKEN_EXPIRY` window.
- To revoke access, delete the local token file or run with `--no-telemetry`
  and re-run the setup wizard to pair again.

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
