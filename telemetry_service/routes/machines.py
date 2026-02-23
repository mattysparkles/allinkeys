from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, List, Optional
from uuid import uuid4

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_machine_for_user, get_ui_current_user
from telemetry_service.error_logging import log_ingest_error, log_raw_snapshot
from telemetry_service.ingest import ingest_seed_events
from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.models import (
    ControlAckRequest,
    ControlCommand,
    ControlCommandList,
    ControlCommandRequest,
    IngestResponse,
    MachineRangeHistory,
    MachineRegisterRequest,
    MachineRegisterResponse,
    MachineSnapshotPoint,
    MachineSnapshotSeries,
    MachineMetricsResponse,
    MachineSummary,
    TelemetryItem,
    UserPublic,
)
from telemetry_service.name_generator import generate_machine_name

router = APIRouter(tags=["Machines"])
logger = logging.getLogger("telemetry.routes.machines")


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1]
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _status_from_last_seen(last_seen: Optional[str], now: datetime) -> str:
    parsed = _parse_timestamp(last_seen)
    if not parsed:
        return "offline"
    delta = now - parsed
    if delta > timedelta(minutes=5):
        return "offline"
    if delta > timedelta(seconds=60):
        return "stalled"
    return "online"


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(cleaned, 0)
        except ValueError:
            pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_json_field(value: Any) -> Any:
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _friendly_machine_name(
    explicit_name: Optional[str],
    identity_name: Optional[str],
    fallback_id: str,
) -> str:
    return explicit_name or identity_name or fallback_id


def _record_sql_statement(
    diagnostics: list[dict[str, Any]] | None,
    statement: str,
    params: tuple[Any, ...],
) -> None:
    if diagnostics is None:
        return
    diagnostics.append(
        {
            "sql": statement.strip(),
            "params": [str(value) for value in params],
        }
    )


def _get_machine_for_user_or_admin(
    machine_id: str,
    current_user: UserPublic,
) -> dict:
    conn = get_db_connection()
    try:
        if current_user.is_admin:
            row = conn.execute(
                """
                SELECT id, user_id, machine_name, machine_identity, gpu_info, version, status, last_seen,
                       keys_per_sec, total_keys, uptime_seconds, mode, process_state,
                       cpu_percent, ram_percent, disk_free_percent, gpu_load_percent,
                       last_error, last_activity, range_recent, range_distribution
                FROM machines
                WHERE id = ?
                """,
                (machine_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, user_id, machine_name, machine_identity, gpu_info, version, status, last_seen,
                       keys_per_sec, total_keys, uptime_seconds, mode, process_state,
                       cpu_percent, ram_percent, disk_free_percent, gpu_load_percent,
                       last_error, last_activity, range_recent, range_distribution
                FROM machines
                WHERE id = ? AND user_id = ?
                """,
                (machine_id, current_user.id),
            ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Machine not found",
            )
        return {
            "id": row[0],
            "user_id": row[1],
            "machine_name": row[2],
            "machine_identity": row[3],
            "gpu_info": row[4],
            "version": row[5],
            "status": row[6],
            "last_seen": row[7],
            "keys_per_sec": row[8],
            "total_keys": row[9],
            "uptime_seconds": row[10],
            "mode": row[11],
            "process_state": row[12],
            "cpu_percent": row[13],
            "ram_percent": row[14],
            "disk_free_percent": row[15],
            "gpu_load_percent": row[16],
            "last_error": row[17],
            "last_activity": row[18],
            "range_recent": _parse_json_field(row[19]),
            "range_distribution": _parse_json_field(row[20]),
        }
    finally:
        conn.close()


@router.post(
    "/register",
    response_model=MachineRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new machine for the authenticated user.",
)
def register_machine(
    payload: MachineRegisterRequest,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineRegisterResponse:
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db_connection()
    payload_data = payload.dict()
    requested_id = (
        payload.machine_id.strip()
        if isinstance(payload.machine_id, str) and payload.machine_id.strip()
        else None
    )
    requested_identity = (
        payload.machine_identity.strip()
        if isinstance(payload.machine_identity, str) and payload.machine_identity.strip()
        else None
    )
    existing_row = None
    if requested_identity:
        existing_row = conn.execute(
            """
            SELECT id, machine_name, machine_identity
            FROM machines
            WHERE machine_identity = ? AND user_id = ?
            LIMIT 1
            """,
            (requested_identity, current_user.id),
        ).fetchone()
    if existing_row is None and requested_id:
        existing_row = conn.execute(
            """
            SELECT id, machine_name, machine_identity
            FROM machines
            WHERE id = ? AND user_id = ?
            LIMIT 1
            """,
            (requested_id, current_user.id),
        ).fetchone()
    if existing_row:
        machine_id, existing_name, existing_identity = existing_row
        machine_identity = requested_identity or existing_identity
        machine_display_name = (
            payload.machine_name
            or existing_name
            or machine_identity
            or generate_machine_name(machine_id)
        )
        try:
            conn.execute(
                """
                UPDATE machines
                SET machine_name = COALESCE(?, machine_name),
                    machine_identity = COALESCE(?, machine_identity),
                    gpu_info = COALESCE(?, gpu_info),
                    version = COALESCE(?, version),
                    status = 'online',
                    last_seen = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    payload.machine_name,
                    requested_identity,
                    payload.gpu_info,
                    payload.version,
                    now,
                    machine_id,
                    current_user.id,
                ),
            )
            conn.commit()
        except Exception as exc:  # pragma: no cover (guards unexpected failures)
            log_ingest_error(
                context="register_machine",
                exc=exc,
                payload=payload_data,
                tables=["machines"],
                conn=conn,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Machine registration failed",
            ) from exc
        finally:
            conn.close()

        with MACHINE_REGISTRY_LOCK:
            MACHINE_REGISTRY[(current_user.id, machine_id)] = {
                "user_id": current_user.id,
                "machine_id": machine_id,
                "machine_name": machine_display_name,
                "machine_identity": machine_identity,
                "last_seen": now,
                "cpu_percent": None,
                "ram_percent": None,
                "disk_free_percent": None,
                "gpu_load_percent": None,
                "gpu_name": payload.gpu_info,
                "time_to_disk_full": None,
            }
        return MachineRegisterResponse(
            machine_id=machine_id, message="Machine registered"
        )

    machine_id = str(uuid4())
    machine_identity = requested_identity or generate_machine_name(machine_id)
    machine_display_name = payload.machine_name or machine_identity
    try:
        conn.execute(
            """
            INSERT INTO machines (
                id, user_id, machine_name, machine_identity, gpu_info, version, status, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, 'online', ?)
            """,
            (
                machine_id,
                current_user.id,
                machine_display_name,
                machine_identity,
                payload.gpu_info,
                payload.version,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        log_ingest_error(
            context="register_machine",
            exc=exc,
            payload=payload_data,
            tables=["machines"],
            conn=conn,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Machine registration failed",
        ) from exc
    except Exception as exc:  # pragma: no cover (guards unexpected failures)
        log_ingest_error(
            context="register_machine",
            exc=exc,
            payload=payload_data,
            tables=["machines"],
            conn=conn,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Machine registration failed",
        ) from exc
    finally:
        conn.close()

    with MACHINE_REGISTRY_LOCK:
        MACHINE_REGISTRY[(current_user.id, machine_id)] = {
            "user_id": current_user.id,
            "machine_id": machine_id,
            "machine_name": machine_display_name,
            "machine_identity": machine_identity,
            "last_seen": now,
            "cpu_percent": None,
            "ram_percent": None,
            "disk_free_percent": None,
            "gpu_load_percent": None,
            "gpu_name": payload.gpu_info,
            "time_to_disk_full": None,
        }
    return MachineRegisterResponse(machine_id=machine_id, message="Machine registered")


def _persist_snapshot(
    *,
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    raw_payload: str,
    machine_id: str,
    user_id: int,
    machine_name: str,
    gpu_info: Optional[str],
    version: Optional[str],
    machine_identity: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> None:
    runtime = _ensure_dict(snapshot.get("runtime"))
    resources = _ensure_dict(snapshot.get("resources"))
    timestamp_iso = snapshot.get("timestamp_iso") or datetime.utcnow().isoformat() + "Z"
    range_recent = snapshot.get("range_recent")
    range_distribution = snapshot.get("range_distribution")
    range_recent_json = (
        json.dumps(range_recent, ensure_ascii=False) if isinstance(range_recent, list) else None
    )
    range_distribution_json = (
        json.dumps(range_distribution, ensure_ascii=False)
        if isinstance(range_distribution, list)
        else None
    )
    # Avoid overwriting existing range data with empty lists from snapshots.
    range_recent_update = (
        range_recent_json
        if isinstance(range_recent, list) and range_recent
        else None
    )
    range_distribution_update = (
        range_distribution_json
        if isinstance(range_distribution, list) and range_distribution
        else None
    )
    insert_sql = """
        INSERT INTO machine_snapshots (
            machine_id, machine_identity, user_id, timestamp, payload,
            keys_per_sec, total_keys, uptime_seconds, mode, process_state,
            cpu_percent, ram_percent, disk_free_percent, gpu_load_percent,
            last_error, last_activity, range_recent, range_distribution
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    insert_params = (
        machine_id,
        machine_identity,
        user_id,
        timestamp_iso,
        raw_payload or "",
        _safe_float(runtime.get("keys_per_sec")),
        _safe_float(runtime.get("total_keys")),
        _safe_float(runtime.get("uptime_seconds")),
        runtime.get("mode"),
        runtime.get("process_state"),
        _safe_float(resources.get("cpu_percent")),
        _safe_float(resources.get("ram_percent")),
        _safe_float(resources.get("disk_free_percent")),
        _safe_float(resources.get("gpu_load_percent")),
        runtime.get("last_error"),
        runtime.get("last_activity_ts"),
        range_recent_json,
        range_distribution_json,
    )
    _record_sql_statement(diagnostics, insert_sql, insert_params)
    conn.execute(insert_sql, insert_params)
    update_sql = """
        UPDATE machines
        SET machine_name = COALESCE(?, machine_name),
            gpu_info = COALESCE(?, gpu_info),
            version = COALESCE(?, version),
            status = 'online',
            last_seen = ?,
            keys_per_sec = ?,
            total_keys = ?,
            uptime_seconds = ?,
            mode = ?,
            process_state = ?,
            cpu_percent = ?,
            ram_percent = ?,
            disk_free_percent = ?,
            gpu_load_percent = ?,
            last_error = ?,
            last_activity = ?,
            range_recent = COALESCE(?, range_recent),
            range_distribution = COALESCE(?, range_distribution),
            machine_identity = COALESCE(?, machine_identity)
        WHERE id = ?
        """
    update_params = (
        machine_name,
        gpu_info,
        version,
        timestamp_iso,
        _safe_float(runtime.get("keys_per_sec")),
        _safe_float(runtime.get("total_keys")),
        _safe_float(runtime.get("uptime_seconds")),
        runtime.get("mode"),
        runtime.get("process_state"),
        _safe_float(resources.get("cpu_percent")),
        _safe_float(resources.get("ram_percent")),
        _safe_float(resources.get("disk_free_percent")),
        _safe_float(resources.get("gpu_load_percent")),
        runtime.get("last_error"),
        runtime.get("last_activity_ts"),
        range_recent_update,
        range_distribution_update,
        machine_identity,
        machine_id,
    )
    _record_sql_statement(diagnostics, update_sql, update_params)
    conn.execute(update_sql, update_params)


@router.post(
    "/{machine_id}/telemetry",
    response_model=IngestResponse,
    summary="Ingest telemetry updates for a registered machine.",
)
def ingest_machine_telemetry(
    machine_id: str,
    items: List[TelemetryItem],
    current_user: UserPublic = Depends(get_ui_current_user),
) -> IngestResponse:
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Expected non-empty list body")

    machine = get_machine_for_user(machine_id, current_user)
    machine_name = machine.get("machine_name") or generate_machine_name(machine_id)

    conn = get_db_connection()
    try:
        with conn:
            ingest_seed_events(
                items,
                current_user=current_user,
                conn=conn,
                machine_id_override=machine_id,
                machine_name_override=machine_name,
            )
    except Exception as exc:  # pragma: no cover (diagnostic logging)
        log_ingest_error(
            context="ingest_machine_telemetry",
            exc=exc,
            payload=[item.dict() for item in items],
            tables=["seed_events", "machines"],
            conn=conn,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest telemetry items",
        ) from exc
    finally:
        conn.close()
    return IngestResponse(status="ok", count=len(items))


@router.api_route(
    "/{machine_id}/snapshot",
    methods=["POST", "PUT"],
    response_model=MachineSummary,
    summary="Ingest a telemetry snapshot for a machine.",
)
async def ingest_machine_snapshot(
    machine_id: str,
    request: Request,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineSummary:
    machine = get_machine_for_user(machine_id, current_user)
    machine_identity = machine.get("machine_identity") or generate_machine_name(machine_id)
    machine_name = machine.get("machine_name") or machine_identity
    gpu_info = machine.get("gpu_info")
    version = machine.get("version")
    timestamp_iso = datetime.utcnow().isoformat() + "Z"

    conn = get_db_connection()
    payload_text = ""
    snapshot_dict: dict[str, Any] = {}
    identity_data: dict[str, Any] = {}
    runtime_data: dict[str, Any] = {}
    resources_data: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []
    try:
        raw_body = await request.body()
        payload_text = raw_body.decode("utf-8", errors="replace")
        log_raw_snapshot(payload=payload_text, machine_id=machine_id)
        if payload_text.strip():
            parsed = json.loads(payload_text)
            if isinstance(parsed, dict):
                snapshot_dict = parsed
        identity_data = _ensure_dict(snapshot_dict.get("identity"))
        runtime_data = _ensure_dict(snapshot_dict.get("runtime"))
        resources_data = _ensure_dict(snapshot_dict.get("resources"))
        machine_name = identity_data.get("machine_name") or machine_name
        machine_identity = identity_data.get("machine_identity") or machine_identity
        gpu_info = resources_data.get("gpu_name") or gpu_info
        version = identity_data.get("client_version") or version
        timestamp_iso = snapshot_dict.get("timestamp_iso") or timestamp_iso

        with conn:
            _persist_snapshot(
                conn=conn,
                snapshot=snapshot_dict,
                raw_payload=payload_text,
                machine_id=machine_id,
                user_id=current_user.id,
                machine_name=machine_name,
                gpu_info=gpu_info,
                version=version,
                machine_identity=machine_identity,
                diagnostics=diagnostics,
            )
    except HTTPException:
        raise
    except Exception as exc:
        statement_summary = diagnostics.copy()
        log_ingest_error(
            context="ingest_machine_snapshot",
            exc=exc,
            payload={
                "machine_id": machine_id,
                "user_id": current_user.id,
                "snapshot": payload_text,
                "statements": statement_summary,
            },
            tables=["machine_snapshots", "machines"],
            conn=conn,
        )
        logger.exception(
            "Snapshot ingest failed | machine_id=%s user_id=%s payload=%s statements=%s",
            machine_id,
            current_user.id,
            payload_text,
            statement_summary,
        )
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
    finally:
        conn.close()

    with MACHINE_REGISTRY_LOCK:
        MACHINE_REGISTRY[(current_user.id, machine_id)] = {
            "user_id": current_user.id,
            "machine_id": machine_id,
            "machine_name": machine_name,
            "machine_identity": machine_identity,
            "last_seen": timestamp_iso,
            "cpu_percent": _safe_float(resources_data.get("cpu_percent")),
            "ram_percent": _safe_float(resources_data.get("ram_percent")),
            "disk_free_percent": _safe_float(resources_data.get("disk_free_percent")),
            "gpu_load_percent": _safe_float(resources_data.get("gpu_load_percent")),
            "gpu_name": resources_data.get("gpu_name"),
            "time_to_disk_full": resources_data.get("time_to_disk_full"),
            "keys_per_sec": _safe_float(runtime_data.get("keys_per_sec")),
            "mode": runtime_data.get("mode"),
            "process_state": runtime_data.get("process_state"),
        }
    try:
        logger.info(
            "Snapshot ingested | user_id=%s machine_id=%s machine_name=%s keys_per_sec=%s",
            current_user.id,
            machine_id,
            machine_name,
            _safe_float(runtime_data.get("keys_per_sec")),
        )
    except Exception:
        pass
    return get_machine(machine_id, current_user=current_user)


@router.get(
    "/me",
    response_model=List[MachineSummary],
    summary="List machines for the authenticated user.",
)
def list_my_machines(
    search: Optional[str] = Query(None, description="Filter by machine name or GPU."),
    include_all: bool = Query(
        False,
        description="Admins only: include all machines in the response.",
    ),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> List[MachineSummary]:
    now = datetime.utcnow()
    conn = get_db_connection()
    try:
        filters = []
        params: List[object] = []
        if not (current_user.is_admin and include_all):
            filters.append("user_id = ?")
            params.append(current_user.id)
        if search:
            filters.append("(LOWER(machine_name) LIKE ? OR LOWER(gpu_info) LIKE ?)")
            like = f"%{search.lower()}%"
            params.extend([like, like])
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = conn.execute(
            f"""
            SELECT id, machine_name, machine_identity, gpu_info, version, last_seen,
                   keys_per_sec, total_keys, uptime_seconds, mode, process_state,
                   cpu_percent, ram_percent, disk_free_percent, gpu_load_percent,
                   last_error, last_activity, range_recent, range_distribution
            FROM machines
            {where_clause}
            ORDER BY machine_name, id
            """,
            params,
        ).fetchall()
        response: List[MachineSummary] = []
        for row in rows:
            (
                machine_id,
                machine_name,
                machine_identity,
                gpu_info,
                version,
                last_seen,
                keys_per_sec,
                total_keys,
                uptime_seconds,
                mode,
                process_state,
                cpu_percent,
                ram_percent,
                disk_free_percent,
                gpu_load_percent,
                last_error,
                last_activity,
                range_recent,
                range_distribution,
            ) = row
            kps_value = _safe_float(keys_per_sec)
            parsed_range_recent = _parse_json_field(range_recent)
            parsed_range_distribution = _parse_json_field(range_distribution)
            display_name = _friendly_machine_name(machine_name, machine_identity, machine_id)
            response.append(
                MachineSummary(
                    id=machine_id,
                    machine_name=display_name,
                    gpu_info=gpu_info,
                    status=_status_from_last_seen(last_seen, now),
                    keys_per_sec=round(kps_value or 0, 2),
                    total_keys=total_keys,
                    uptime_seconds=uptime_seconds,
                    mode=mode,
                    process_state=process_state,
                    cpu_percent=cpu_percent,
                    ram_percent=ram_percent,
                    disk_free_percent=disk_free_percent,
                    gpu_load_percent=gpu_load_percent,
                    last_error=last_error,
                    last_activity=last_activity,
                    last_seen=last_seen,
                    version=version,
                    range_recent=parsed_range_recent,
                    range_distribution=parsed_range_distribution,
                    identity_name=machine_identity,
                )
            )
        return response
    finally:
        conn.close()


@router.get(
    "/{machine_id}",
    response_model=MachineSummary,
    summary="Fetch details for a single machine.",
)
def get_machine(
    machine_id: str,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineSummary:
    now = datetime.utcnow()
    machine = _get_machine_for_user_or_admin(machine_id, current_user)
    kps_value = _safe_float(machine.get("keys_per_sec"))
    display_name = _friendly_machine_name(
        machine.get("machine_name"),
        machine.get("machine_identity"),
        machine_id,
    )
    return MachineSummary(
        id=machine["id"],
        machine_name=display_name,
        gpu_info=machine.get("gpu_info"),
        status=_status_from_last_seen(machine.get("last_seen"), now),
        keys_per_sec=round(kps_value or 0, 2),
        total_keys=machine.get("total_keys"),
        uptime_seconds=machine.get("uptime_seconds"),
        mode=machine.get("mode"),
        process_state=machine.get("process_state"),
        cpu_percent=machine.get("cpu_percent"),
        ram_percent=machine.get("ram_percent"),
        disk_free_percent=machine.get("disk_free_percent"),
        gpu_load_percent=machine.get("gpu_load_percent"),
        last_error=machine.get("last_error"),
        last_activity=machine.get("last_activity"),
        last_seen=machine.get("last_seen"),
        version=machine.get("version"),
        range_recent=machine.get("range_recent"),
        range_distribution=machine.get("range_distribution"),
        identity_name=machine.get("machine_identity"),
    )


@router.get(
    "/summary",
    summary="Return aggregate telemetry summary for the current user.",
)
def machine_summary(
    current_user: UserPublic = Depends(get_ui_current_user),
) -> dict:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT status, keys_per_sec, total_keys, cpu_percent, ram_percent, gpu_load_percent
            FROM machines
            WHERE user_id = ?
            """,
            (current_user.id,),
        ).fetchall()
    finally:
        conn.close()
    total = len(rows)
    online = len([r for r in rows if r[0] == "online"])
    kps_values = [r[1] or 0 for r in rows]
    total_keys = sum((r[2] or 0) for r in rows)
    cpu_values = [r[3] for r in rows if r[3] is not None]
    ram_values = [r[4] for r in rows if r[4] is not None]
    gpu_values = [r[5] for r in rows if r[5] is not None]
    return {
        "machine_count": total,
        "online_count": online,
        "total_keys_per_sec": round(sum(kps_values), 2),
        "avg_keys_per_sec": round(sum(kps_values) / total, 2) if total else 0,
        "total_keys": total_keys,
        "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else None,
        "avg_ram_percent": round(sum(ram_values) / len(ram_values), 2) if ram_values else None,
        "avg_gpu_percent": round(sum(gpu_values) / len(gpu_values), 2) if gpu_values else None,
    }


@router.get(
    "/{machine_id}/snapshots",
    response_model=MachineSnapshotSeries,
    summary="Return recent telemetry snapshots for a machine.",
)
def list_machine_snapshots(
    machine_id: str,
    minutes: int = Query(60, ge=1, le=1440),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineSnapshotSeries:
    _get_machine_for_user_or_admin(machine_id, current_user)
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + "Z"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, keys_per_sec, cpu_percent, ram_percent, gpu_load_percent
            FROM machine_snapshots
            WHERE machine_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (machine_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    return MachineSnapshotSeries(
        machine_id=machine_id,
        points=[
            MachineSnapshotPoint(
                timestamp=row[0],
                keys_per_sec=row[1],
                cpu_percent=row[2],
                ram_percent=row[3],
                gpu_load_percent=row[4],
            )
            for row in rows
        ],
    )


@router.get(
    "/{machine_id}/metrics",
    response_model=MachineMetricsResponse,
    summary="Return the latest full metrics payload for a machine.",
)
def machine_metrics(
    machine_id: str,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineMetricsResponse:
    _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT timestamp, payload
            FROM machine_snapshots
            WHERE machine_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (machine_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return MachineMetricsResponse(machine_id=machine_id, timestamp=None, metrics={})
    snapshot_ts, payload = row
    parsed = _parse_json_field(payload)
    if not isinstance(parsed, dict):
        return MachineMetricsResponse(machine_id=machine_id, timestamp=snapshot_ts, metrics={})
    metrics = parsed.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    timestamp = parsed.get("timestamp_iso") or snapshot_ts
    return MachineMetricsResponse(
        machine_id=machine_id,
        timestamp=timestamp,
        metrics=metrics,
    )


@router.get(
    "/{machine_id}/ranges/recent",
    response_model=MachineRangeHistory,
    summary="Return recent range metadata reported by a machine.",
)
def list_machine_ranges(
    machine_id: str,
    limit: int = Query(20, ge=1, le=200),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> MachineRangeHistory:
    machine = _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, range_recent
            FROM machine_snapshots
            WHERE machine_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (machine_id, max(1, min(limit * 3, 200))),
        ).fetchall()
    finally:
        conn.close()
    ranges: list[dict[str, Any]] = []
    for snapshot_ts, payload in rows:
        parsed = _parse_json_field(payload)
        if not isinstance(parsed, list):
            continue
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            ranges.append(
                {
                    "range_id": entry.get("range_id"),
                    "start": _safe_int(entry.get("start")),
                    "end": _safe_int(entry.get("end")),
                    "position": _safe_int(entry.get("position")),
                    "normalized_position": _safe_float(entry.get("normalized_position")),
                    "normalized_span": _safe_float(entry.get("normalized_span")),
                    "space_min": _safe_int(entry.get("space_min")),
                    "space_max": _safe_int(entry.get("space_max")),
                    "timestamp_iso": entry.get("timestamp_iso") or snapshot_ts,
                    "source": "snapshot",
                }
            )
            if len(ranges) >= limit:
                break
        if len(ranges) >= limit:
            break
    return MachineRangeHistory(
        machine_id=machine_id,
        machine_name=_friendly_machine_name(
            machine.get("machine_name"),
            machine.get("machine_identity"),
            machine_id,
        ),
        identity_name=machine.get("machine_identity"),
        ranges=ranges[:limit],
    )


@router.post(
    "/{machine_id}/control",
    response_model=ControlCommand,
    summary="Issue a control command to a machine.",
)
def create_control_command(
    machine_id: str,
    payload: ControlCommandRequest,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> ControlCommand:
    _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_control (machine_id, command, value)
                VALUES (?, ?, ?)
                """,
                (machine_id, payload.command, payload.value),
            )
        row = conn.execute(
            """
            SELECT id, machine_id, command, value, issued_at, status
            FROM pending_control
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store control command",
        )
    return ControlCommand(
        id=row[0],
        machine_id=row[1],
        command=row[2],
        value=row[3],
        issued_at=row[4],
        status=row[5],
    )


@router.get(
    "/{machine_id}/control",
    response_model=ControlCommandList,
    summary="List recent control commands for a machine.",
)
def list_control_commands(
    machine_id: str,
    status_filter: Optional[str] = Query(
        None, description="Filter commands by status."
    ),
    limit: int = Query(10, ge=1, le=100),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> ControlCommandList:
    _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        params: List[object] = [machine_id]
        status_clause = ""
        if status_filter:
            status_clause = "AND status = ?"
            params.append(status_filter)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT id, machine_id, command, value, issued_at, status
            FROM pending_control
            WHERE machine_id = ? {status_clause}
            ORDER BY issued_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    return ControlCommandList(
        commands=[
            ControlCommand(
                id=row[0],
                machine_id=row[1],
                command=row[2],
                value=row[3],
                issued_at=row[4],
                status=row[5],
            )
            for row in rows
        ]
    )


@router.get(
    "/{machine_id}/control/poll",
    response_model=ControlCommandList,
    summary="Poll pending control commands for a machine.",
)
def poll_control_commands(
    machine_id: str,
    limit: int = Query(20, ge=1, le=100),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> ControlCommandList:
    _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, machine_id, command, value, issued_at, status
            FROM pending_control
            WHERE machine_id = ? AND status = 'pending'
            ORDER BY issued_at ASC
            LIMIT ?
            """,
            (machine_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return ControlCommandList(
        commands=[
            ControlCommand(
                id=row[0],
                machine_id=row[1],
                command=row[2],
                value=row[3],
                issued_at=row[4],
                status=row[5],
            )
            for row in rows
        ]
    )


@router.post(
    "/{machine_id}/control/ack",
    response_model=ControlCommand,
    summary="Acknowledge a control command for a machine.",
)
def acknowledge_control_command(
    machine_id: str,
    payload: ControlAckRequest,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> ControlCommand:
    _get_machine_for_user_or_admin(machine_id, current_user)
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, machine_id, command, value, issued_at, status
            FROM pending_control
            WHERE id = ? AND machine_id = ?
            """,
            (payload.command_id, machine_id),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Control command not found",
            )
        with conn:
            conn.execute(
                """
                UPDATE pending_control
                SET status = 'acknowledged'
                WHERE id = ?
                """,
                (payload.command_id,),
            )
        updated = conn.execute(
            """
            SELECT id, machine_id, command, value, issued_at, status
            FROM pending_control
            WHERE id = ?
            """,
            (payload.command_id,),
        ).fetchone()
    finally:
        conn.close()
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update control command",
        )
    return ControlCommand(
        id=updated[0],
        machine_id=updated[1],
        command=updated[2],
        value=updated[3],
        issued_at=updated[4],
        status=updated[5],
    )
