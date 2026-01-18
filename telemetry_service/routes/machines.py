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
    ControlAckRequest,
    ControlCommand,
    ControlCommandList,
    ControlCommandRequest,
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


def _get_machine_for_user_or_admin(
    machine_id: str,
    current_user: UserPublic,
) -> dict:
    conn = get_db_connection()
    try:
        if current_user.is_admin:
            row = conn.execute(
                """
                SELECT id, user_id, machine_name, gpu_info, version, status, last_seen
                FROM machines
                WHERE id = ?
                """,
                (machine_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, user_id, machine_name, gpu_info, version, status, last_seen
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
            "gpu_info": row[3],
            "version": row[4],
            "status": row[5],
            "last_seen": row[6],
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


@router.post(
    "/{machine_id}/control",
    response_model=ControlCommand,
    summary="Issue a control command to a machine.",
)
def create_control_command(
    machine_id: str,
    payload: ControlCommandRequest,
    current_user: UserPublic = Depends(get_current_user),
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
    current_user: UserPublic = Depends(get_current_user),
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
    current_user: UserPublic = Depends(get_current_user),
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
    current_user: UserPublic = Depends(get_current_user),
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
