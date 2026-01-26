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
        logger.warning(
            "%s missing for machine_id=%s user_id=%s; nothing to store",
            name,
            source_id,
            user_id,
        )
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
    for item in items:
        payload_keys = sorted(item.dict(exclude_none=True).keys())
        source_key = (
            machine_id_override or item.machine_id or item.app_instance_id
        ) or "unknown"
        logger.info(
            "SEED_PAYLOAD machine=%s keys=%s",
            source_key,
            payload_keys,
        )
        logger.info(
            "Range metadata presence machine_key=%s range_recent=%s range_distribution=%s",
            source_key,
            item.range_recent is not None,
            item.range_distribution is not None,
        )
        ts = item.timestamp_iso or now
        machine_key = machine_id_override or item.machine_id or item.app_instance_id
        candidate_name = machine_name_override or item.machine_name
        machine_name = candidate_name
        range_recent_json = _serialize_range_field(
            "range_recent", item.range_recent, machine_key, current_user.id
        )
        range_distribution_json = _serialize_range_field(
            "range_distribution", item.range_distribution, machine_key, current_user.id
        )
        if machine_key:
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
                    candidate_name
                    or existing.get("machine_name")
                    or machine_identity
                )
                MACHINE_REGISTRY[registry_key] = {
                    "user_id": current_user.id,
                    "machine_id": machine_key,
                    "machine_identity": machine_identity,
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
                    item.gpu_name,
                    item.client_version,
                    ts,
                    range_recent_json,
                    range_distribution_json,
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
                range_recent_json,
                range_distribution_json,
                json.dumps(item.reference_overlays)
                if item.reference_overlays
                else None,
            ),
        )
