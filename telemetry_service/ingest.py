from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.models import TelemetryItem, UserPublic
from telemetry_service.name_generator import generate_machine_name

logger = logging.getLogger("telemetry.ingest")


def _serialize_range_field(
    name: str,
    payload: Optional[list[dict[str, object]]],
    machine_key: Optional[str],
    user_id: int,
) -> Optional[str]:
    source_id = machine_key or "unknown"
    if payload is None:
        return None
    if not isinstance(payload, list):
        logger.warning(
            "%s malformed for machine_id=%s user_id=%s; expected list got %s",
            name,
            source_id,
            user_id,
            type(payload).__name__,
        )
        return None
    if not payload:
        logger.warning(
            "%s empty list for machine_id=%s user_id=%s; storing empty array",
            name,
            source_id,
            user_id,
        )
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "%s serialization failed for machine_id=%s user_id=%s: %s",
            name,
            source_id,
            user_id,
            exc,
        )
        return None


def _fetch_machine_identity(
    conn: sqlite3.Connection, user_id: int, machine_key: str
) -> Optional[str]:
    row = conn.execute(
        "SELECT machine_identity FROM machines WHERE id = ? AND user_id = ? LIMIT 1",
        (machine_key, user_id),
    ).fetchone()
    return row[0] if row and row[0] else None


def ingest_seed_events(
    items: list[TelemetryItem],
    *,
    current_user: UserPublic,
    conn: sqlite3.Connection,
    machine_id_override: Optional[str] = None,
    machine_name_override: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    pending_rows: list[dict[str, object]] = []
    machine_updates: dict[str, dict[str, object]] = {}
    resolved_names: dict[str, str] = {}

    for item in items:
        ts = item.timestamp_iso or now
        machine_key = machine_id_override or item.machine_id or item.app_instance_id
        candidate_name = machine_name_override or item.machine_name
        range_recent_json = _serialize_range_field(
            "range_recent", item.range_recent, machine_key, current_user.id
        )
        range_distribution_json = _serialize_range_field(
            "range_distribution", item.range_distribution, machine_key, current_user.id
        )

        if machine_key:
            update = machine_updates.get(machine_key, {})
            update.update(
                {
                    "machine_key": machine_key,
                    "candidate_name": candidate_name,
                    "ts": ts,
                    "gpu_name": item.gpu_name,
                    "client_version": item.client_version,
                    "cpu_percent": item.cpu_percent,
                    "ram_percent": item.ram_percent,
                    "disk_free_percent": item.disk_free_percent,
                    "gpu_load_percent": item.gpu_load_percent,
                    "time_to_disk_full": item.time_to_disk_full,
                }
            )
            if range_recent_json is not None:
                update["range_recent_json"] = range_recent_json
            if range_distribution_json is not None:
                update["range_distribution_json"] = range_distribution_json
            machine_updates[machine_key] = update

        pending_rows.append(
            {
                "user_id": current_user.id,
                "seed_fingerprint": item.seed_fingerprint,
                "app_instance_id": item.app_instance_id,
                "client_version": item.client_version,
                "mode": item.mode,
                "range_id": item.range_id,
                "ts": ts,
                "used": 1 if item.used else 0,
                "match_found": 1 if item.match_found else 0,
                "machine_key": machine_key,
                "candidate_name": candidate_name,
                "range_recent_json": range_recent_json,
                "range_distribution_json": range_distribution_json,
                "reference_overlays": json.dumps(item.reference_overlays)
                if item.reference_overlays
                else None,
            }
        )

    for machine_key, update in machine_updates.items():
        with MACHINE_REGISTRY_LOCK:
            registry_key = (current_user.id, machine_key)
            existing = MACHINE_REGISTRY.get(registry_key, {})
            machine_identity = existing.get("machine_identity")
            if not machine_identity:
                machine_identity = _fetch_machine_identity(
                    conn, current_user.id, machine_key
                )
            if not machine_identity:
                machine_identity = generate_machine_name(machine_key)
            machine_name = (
                update.get("candidate_name")
                or existing.get("machine_name")
                or machine_identity
            )
            MACHINE_REGISTRY[registry_key] = {
                "user_id": current_user.id,
                "machine_id": machine_key,
                "machine_identity": machine_identity,
                "machine_name": machine_name,
                "last_seen": update.get("ts"),
                "cpu_percent": update.get("cpu_percent"),
                "ram_percent": update.get("ram_percent"),
                "disk_free_percent": update.get("disk_free_percent"),
                "gpu_load_percent": update.get("gpu_load_percent"),
                "gpu_name": update.get("gpu_name"),
                "time_to_disk_full": update.get("time_to_disk_full"),
            }
        conn.execute(
            """
            INSERT INTO machines (
                    id, user_id, machine_name, machine_identity, gpu_info, version, status, last_seen,
                    range_recent, range_distribution
                ) VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    machine_name=COALESCE(excluded.machine_name, machines.machine_name),
                    machine_identity=COALESCE(machines.machine_identity, excluded.machine_identity),
                    gpu_info=COALESCE(excluded.gpu_info, machines.gpu_info),
                    version=COALESCE(excluded.version, machines.version),
                    status='online',
                    last_seen=excluded.last_seen,
                    range_recent=COALESCE(excluded.range_recent, machines.range_recent),
                    range_distribution=COALESCE(excluded.range_distribution, machines.range_distribution)
            """,
            (
                machine_key,
                current_user.id,
                machine_name,
                machine_identity,
                update.get("gpu_name"),
                update.get("client_version"),
                update.get("ts"),
                update.get("range_recent_json"),
                update.get("range_distribution_json"),
            ),
        )
        if machine_key:
            resolved_names[machine_key] = machine_name

    seed_rows: list[tuple] = []
    for row in pending_rows:
        machine_key = row.get("machine_key")
        resolved = resolved_names.get(machine_key) if machine_key else None
        machine_name = resolved or row.get("candidate_name")
        seed_rows.append(
            (
                row.get("user_id"),
                row.get("seed_fingerprint"),
                row.get("app_instance_id"),
                row.get("client_version"),
                row.get("mode"),
                row.get("range_id"),
                row.get("ts"),
                row.get("ts"),
                row.get("used"),
                row.get("match_found"),
                machine_key,
                machine_name,
                row.get("range_recent_json"),
                row.get("range_distribution_json"),
                row.get("reference_overlays"),
            )
        )

    conn.executemany(
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
        seed_rows,
    )
