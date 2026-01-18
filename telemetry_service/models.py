from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8)


class UserPublic(BaseModel):
    id: int
    username: str
    created_at: str
    is_admin: bool = False


class TelemetryItem(BaseModel):
    app_instance_id: Optional[str] = None
    client_version: Optional[str] = None
    mode: Optional[str] = None
    range_id: Optional[str] = None
    seed_fingerprint: str
    timestamp_iso: Optional[str] = None
    used: Optional[bool] = False
    match_found: Optional[bool] = False
    machine_id: Optional[str] = None
    machine_name: Optional[str] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    disk_free_percent: Optional[float] = None
    gpu_load_percent: Optional[float] = None
    gpu_name: Optional[str] = None
    time_to_disk_full: Optional[str] = None
    range_recent: Optional[List[Dict[str, Any]]] = None
    range_distribution: Optional[List[Dict[str, Any]]] = None
    reference_overlays: Optional[List[Dict[str, Any]]] = None


class IngestResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    count: int = Field(..., examples=[1])


class MachineRegisterRequest(BaseModel):
    machine_name: str
    gpu_info: Optional[str] = None
    version: Optional[str] = None


class MachineRegisterResponse(BaseModel):
    machine_id: str
    message: str
