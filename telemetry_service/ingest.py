from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.models import TelemetryItem, UserPublic


def ingest_seed_events(
    items: list[TelemetryItem],
    *,
    current_user: UserPublic,
    conn: sqlite3.Connection,
    machine_id_override: Optional[str] = None,
    machine_name_override: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    for item in items:
        ts = item.timestamp_iso or now
        machine_key = machine_id_override or item.machine_id or item.app_instance_id
        machine_name = machine_name_override or item.machine_name
        if machine_key:
            with MACHINE_REGISTRY_LOCK:
                registry_key = (current_user.id, machine_key)
                existing = MACHINE_REGISTRY.get(registry_key, {})
                machine_name = (
                    machine_name or existing.get("machine_name") or machine_key
                )
                MACHINE_REGISTRY[registry_key] = {
                    "user_id": current_user.id,
                    "machine_id": machine_key,
                    "machine_name": machine_name,
                    "last_seen": ts,
                    "cpu_percent": item.cpu_percent,
                    "ram_percent": item.ram_percent,
                    "disk_free_percent": item.disk_free_percent,
                    "gpu_load_percent": item.gpu_load_percent,
                    "gpu_name": item.gpu_name,
                    "time_to_disk_full": item.time_to_disk_full,
                }
            conn.execute(
                """
                INSERT INTO machines (
                    id, user_id, machine_name, gpu_info, version, status, last_seen
                ) VALUES (?, ?, ?, ?, ?, 'online', ?)
                ON CONFLICT(id) DO UPDATE SET
                    machine_name=COALESCE(excluded.machine_name, machines.machine_name),
                    gpu_info=COALESCE(excluded.gpu_info, machines.gpu_info),
                    version=COALESCE(excluded.version, machines.version),
                    status='online',
                    last_seen=excluded.last_seen
                """,
                (
                    machine_key,
                    current_user.id,
                    machine_name,
                    item.gpu_name,
                    item.client_version,
                    ts,
                ),
            )
        conn.execute(
            """
            INSERT INTO seed_events (
                user_id, seed_fingerprint, app_instance_id, client_version, mode, range_id,
                first_seen, last_seen, used, match_found, machine_id, machine_name,
                range_recent, range_distribution, reference_overlays
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(seed_fingerprint, range_id, user_id) DO UPDATE SET
                last_seen=excluded.last_seen,
                used=MAX(seed_events.used, excluded.used),
                match_found=MAX(seed_events.match_found, excluded.match_found),
                app_instance_id=COALESCE(excluded.app_instance_id, seed_events.app_instance_id),
                client_version=COALESCE(excluded.client_version, seed_events.client_version),
                mode=COALESCE(excluded.mode, seed_events.mode),
                machine_id=COALESCE(excluded.machine_id, seed_events.machine_id),
                machine_name=COALESCE(excluded.machine_name, seed_events.machine_name),
                range_recent=COALESCE(excluded.range_recent, seed_events.range_recent),
                range_distribution=COALESCE(excluded.range_distribution, seed_events.range_distribution),
                reference_overlays=COALESCE(excluded.reference_overlays, seed_events.reference_overlays)
            """,
            (
                current_user.id,
                item.seed_fingerprint,
                item.app_instance_id,
                item.client_version,
                item.mode,
                item.range_id,
                ts,
                ts,
                1 if item.used else 0,
                1 if item.match_found else 0,
                machine_key,
                machine_name,
                json.dumps(item.range_recent) if item.range_recent else None,
                json.dumps(item.range_distribution) if item.range_distribution else None,
                json.dumps(item.reference_overlays)
                if item.reference_overlays
                else None,
            ),
        )
