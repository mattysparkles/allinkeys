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

## Opt‑out

Telemetry is enabled by default.  To disable it, run the application with the
`--no-telemetry` CLI flag:

```bash
python main.py --no-telemetry
```

When disabled, no events are recorded and the queue remains empty.

## Offline Behaviour

Events are written to a durable SQLite queue located under `logs/`.  If the
machine is offline, events accumulate and are uploaded the next time a network
connection is available.  The queue is capped at 100k entries and older records
are discarded in a ring‑buffer fashion.

