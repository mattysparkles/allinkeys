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

For POST ingestion:

```bash
curl -X POST http://localhost:3088/v1/seed \
  -H "Content-Type: application/json" \
  -H "X-API-Key: changeme" \
  -d '[{"seed_fingerprint":"abc123","used":true}]'
```

## Security tips

* Deploy behind a TLS-terminating reverse proxy (nginx, Caddy, or Traefik).
* Restrict access to the telemetry service with firewall rules or private network rules.
* Rotate the API key periodically and store it in a secrets manager when possible.
* Monitor logs for repeated 401 responses to detect misuse.
