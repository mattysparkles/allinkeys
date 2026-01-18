from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_current_user, get_machine_for_user
from telemetry_service.ingest import ingest_seed_events
from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.models import (
    IngestResponse,
    MachineRegisterRequest,
    MachineRegisterResponse,
    MachineSummary,
    TelemetryItem,
    UserPublic,
)

router = APIRouter(prefix="/v1/machines", tags=["Machines"])


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


@router.post(
    "/register",
    response_model=MachineRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new machine for the authenticated user.",
)
def register_machine(
    payload: MachineRegisterRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> MachineRegisterResponse:
    machine_id = str(uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO machines (
                id, user_id, machine_name, gpu_info, version, status, last_seen
            ) VALUES (?, ?, ?, ?, ?, 'online', ?)
            """,
            (
                machine_id,
                current_user.id,
                payload.machine_name,
                payload.gpu_info,
                payload.version,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Machine registration failed",
        ) from exc
    finally:
        conn.close()

    with MACHINE_REGISTRY_LOCK:
        MACHINE_REGISTRY[(current_user.id, machine_id)] = {
            "user_id": current_user.id,
            "machine_id": machine_id,
            "machine_name": payload.machine_name,
            "last_seen": now,
            "cpu_percent": None,
            "ram_percent": None,
            "disk_free_percent": None,
            "gpu_load_percent": None,
            "gpu_name": payload.gpu_info,
            "time_to_disk_full": None,
        }
    return MachineRegisterResponse(machine_id=machine_id, message="Machine registered")


@router.post(
    "/{machine_id}/telemetry",
    response_model=IngestResponse,
    summary="Ingest telemetry updates for a registered machine.",
)
def ingest_machine_telemetry(
    machine_id: str,
    items: List[TelemetryItem],
    current_user: UserPublic = Depends(get_current_user),
) -> IngestResponse:
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Expected non-empty list body")

    machine = get_machine_for_user(machine_id, current_user)
    machine_name = machine.get("machine_name") or machine_id

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
    finally:
        conn.close()
    return IngestResponse(status="ok", count=len(items))


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
    current_user: UserPublic = Depends(get_current_user),
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
            SELECT id, machine_name, gpu_info, version, last_seen
            FROM machines
            {where_clause}
            ORDER BY machine_name, id
            """,
            params,
        ).fetchall()
        cutoff = (now - timedelta(seconds=60)).isoformat() + "Z"
        response: List[MachineSummary] = []
        for row in rows:
            machine_id, machine_name, gpu_info, version, last_seen = row
            kps_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM seed_events
                WHERE machine_id = ? AND last_seen >= ?
                """,
                (machine_id, cutoff),
            ).fetchone()[0]
            response.append(
                MachineSummary(
                    id=machine_id,
                    machine_name=machine_name or machine_id,
                    gpu_info=gpu_info,
                    status=_status_from_last_seen(last_seen, now),
                    keys_per_sec=round(kps_count / 60, 2) if kps_count else 0,
                    last_seen=last_seen,
                    version=version,
                )
            )
        return response
    finally:
        conn.close()
