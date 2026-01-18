from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_current_user, get_machine_for_user
from telemetry_service.ingest import ingest_seed_events
from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.models import (
    IngestResponse,
    MachineRegisterRequest,
    MachineRegisterResponse,
    TelemetryItem,
    UserPublic,
)

router = APIRouter(prefix="/v1/machines", tags=["Machines"])


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
