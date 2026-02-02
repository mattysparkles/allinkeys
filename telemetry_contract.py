"""Shared telemetry schema for local and web dashboards."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ControlCapabilities(BaseModel):
    pause: bool = True
    resume: bool = True
    stop: bool = True
    restart: bool = True
    set_mode: bool = True
    set_range: bool = True


class MachineIdentity(BaseModel):
    machine_id: str
    machine_name: Optional[str] = None
    machine_identity: Optional[str] = None
    display_name: Optional[str] = None
    app_instance_id: Optional[str] = None
    client_version: Optional[str] = None


class RuntimeStats(BaseModel):
    mode: Optional[str] = None
    keys_per_sec: Optional[float] = None
    total_keys: Optional[float] = None
    uptime_seconds: Optional[float] = None
    process_state: Optional[str] = None
    last_activity_ts: Optional[str] = None
    last_error: Optional[str] = None


class ResourceStats(BaseModel):
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    disk_free_percent: Optional[float] = None
    gpu_load_percent: Optional[float] = None
    gpu_name: Optional[str] = None
    time_to_disk_full: Optional[str] = None


class MachineTelemetrySnapshot(BaseModel):
    identity: MachineIdentity
    runtime: RuntimeStats
    resources: ResourceStats
    capabilities: ControlCapabilities = Field(default_factory=ControlCapabilities)
    range_tag: Optional[str] = None
    range_start: Optional[int] = None
    range_end: Optional[int] = None
    timestamp_iso: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    metrics: Optional[Dict[str, Any]] = None
    range_recent: Optional[List[Dict[str, Any]]] = None
    range_distribution: Optional[List[Dict[str, Any]]] = None
    reference_overlays: Optional[List[Dict[str, Any]]] = None


class MachineTelemetrySummary(BaseModel):
    identity: MachineIdentity
    runtime: RuntimeStats
    resources: ResourceStats
    status: str = "offline"
