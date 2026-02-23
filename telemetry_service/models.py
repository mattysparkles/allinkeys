from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PairInitResponse(BaseModel):
    pair_code: str
    pair_url: str
    poll_interval_seconds: int


class PairStatusResponse(BaseModel):
    status: str
    token: Optional[str] = None


class PairClaimRequest(BaseModel):
    pair_code: str


class PairClaimResponse(BaseModel):
    status: str
    token: Optional[str] = None
    message: Optional[str] = None


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
    machine_id: Optional[str] = None
    machine_identity: Optional[str] = None
    machine_name: Optional[str] = None
    gpu_info: Optional[str] = None
    version: Optional[str] = None


class MachineRegisterResponse(BaseModel):
    machine_id: str
    message: str


class MachineSummary(BaseModel):
    id: str
    machine_name: Optional[str] = None
    gpu_info: Optional[str] = None
    status: str
    keys_per_sec: float = 0
    total_keys: Optional[float] = None
    uptime_seconds: Optional[float] = None
    mode: Optional[str] = None
    process_state: Optional[str] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    disk_free_percent: Optional[float] = None
    gpu_load_percent: Optional[float] = None
    last_error: Optional[str] = None
    last_activity: Optional[str] = None
    last_seen: Optional[str] = None
    version: Optional[str] = None
    range_recent: Optional[List[Dict[str, Any]]] = None
    range_distribution: Optional[List[Dict[str, Any]]] = None
    identity_name: Optional[str] = None

class MachineRangeObservation(BaseModel):
    range_id: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    position: Optional[int] = None
    normalized_position: Optional[float] = None
    normalized_span: Optional[float] = None
    space_min: Optional[int] = None
    space_max: Optional[int] = None
    timestamp_iso: Optional[str] = None
    source: Optional[str] = None


class MachineRangeHistory(BaseModel):
    machine_id: str
    machine_name: Optional[str] = None
    identity_name: Optional[str] = None
    ranges: List[MachineRangeObservation] = Field(default_factory=list)


class AdminUserSummary(BaseModel):
    id: int
    username: str
    machine_count: int
    avg_kps: float
    coverage_percent: float


class AdminMachineSummary(BaseModel):
    id: str
    machine_name: Optional[str] = None
    user_id: int
    username: str
    gpu_info: Optional[str] = None
    status: str
    keys_per_sec: float = 0
    last_seen: Optional[str] = None
    version: Optional[str] = None


class AdminKeyspaceProgress(BaseModel):
    total_ranges: int
    total_submissions: int
    unique_seed_count: int
    coverage_percent: float
    window_start: Optional[str] = None
    window_end: Optional[str] = None


class ControlCommandRequest(BaseModel):
    command: Literal[
        "pause",
        "resume",
        "stop",
        "restart",
        "set_mode",
        "set_range",
        "queue_seed",
    ]
    value: Optional[str] = None


class ControlCommand(BaseModel):
    id: int
    machine_id: str
    command: str
    value: Optional[str] = None
    issued_at: str
    status: str


class ControlCommandList(BaseModel):
    commands: List[ControlCommand]


class ControlAckRequest(BaseModel):
    command_id: int


class SeedQueueCreateRequest(BaseModel):
    name: str


class SeedQueueList(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    item_count: int = 0


class SeedQueueListResponse(BaseModel):
    queues: List[SeedQueueList]


class SeedQueueItemCreateRequest(BaseModel):
    range_id: Optional[str] = None
    range_value: Optional[str] = None
    seed_start: Optional[str] = None
    seed_end: Optional[str] = None
    position_percent: Optional[float] = None


class SeedQueueItem(BaseModel):
    id: int
    queue_id: int
    range_id: Optional[str] = None
    range_value: Optional[str] = None
    seed_start: Optional[str] = None
    seed_end: Optional[str] = None
    position_percent: Optional[float] = None
    created_at: str


class SeedQueueItemList(BaseModel):
    queue_id: int
    items: List[SeedQueueItem]


class SeedQueuePushRequest(BaseModel):
    mode: Literal["single", "split"]
    machine_id: Optional[str] = None
    machine_ids: Optional[List[str]] = None
    clear_after: bool = False


class SeedQueuePushResponse(BaseModel):
    queue_id: int
    mode: str
    dispatched: int
    machines: List[str]


class TimeSeriesPoint(BaseModel):
    timestamp: str
    value: float


class TimeSeriesResponse(BaseModel):
    metric: str
    bucket_minutes: int
    points: List[TimeSeriesPoint]


class MachineSnapshotPoint(BaseModel):
    timestamp: str
    keys_per_sec: Optional[float] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    gpu_load_percent: Optional[float] = None


class MachineSnapshotSeries(BaseModel):
    machine_id: str
    points: List[MachineSnapshotPoint]


class MachineMetricsResponse(BaseModel):
    machine_id: str
    timestamp: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
