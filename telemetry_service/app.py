from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.telemetry import TOKEN_EXPIRY
from telemetry_service.auth import create_access_token, hash_password, verify_password
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_current_user, get_optional_user
from telemetry_service.error_logging import log_ingest_error
from telemetry_service.ingest import ingest_seed_events
from telemetry_service.machine_registry import MACHINE_REGISTRY, MACHINE_REGISTRY_LOCK
from telemetry_service.routes.dashboard import router as dashboard_router
from telemetry_service.routes.admin import router as admin_router
from telemetry_service.routes.auth_ui import router as auth_ui_router
from telemetry_service.routes.machines import router as machines_router
from telemetry_service.routes.auth_ui import router as auth_ui_router
from telemetry_service.routes.pairing import router as pairing_router
from telemetry_service.routes.pairing import ui_router as pairing_ui_router
from telemetry_service.models import (
    IngestResponse,
    TelemetryItem,
    TokenResponse,
    UserCreate,
    UserPublic,
)

API_KEY_ENV = "TELEMETRY_API_KEY"
logger = logging.getLogger("telemetry")
logging.basicConfig(level=os.getenv("TELEMETRY_LOG_LEVEL", "INFO"))


class MachineInfo(BaseModel):
    machine_id: Optional[str] = None
    machine_name: Optional[str] = None
    last_seen: Optional[str] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    disk_free_percent: Optional[float] = None
    gpu_load_percent: Optional[float] = None
    gpu_name: Optional[str] = None
    time_to_disk_full: Optional[str] = None


class MachineSeriesPoint(BaseModel):
    bucket: str
    machines: int


class MachineStatsResponse(BaseModel):
    slug: str
    machines: List[MachineInfo]
    granularity: str = "hour"
    window: int = 24
    series: List[MachineSeriesPoint] = Field(default_factory=list)


class SeedStatsResponse(BaseModel):
    total_seeds: int
    unique_seed_count: int
    by_mode: Dict[str, int]
    last_seen: Optional[str] = None
    since: Optional[str] = None
    mode: Optional[str] = None


class SeedRangeInfo(BaseModel):
    range_id: Optional[str] = None
    count: int
    match_found: int
    unique_seed_count: int


class SeedRangeResponse(BaseModel):
    ranges: List[SeedRangeInfo]
    since: Optional[str] = None
    mode: Optional[str] = None
    limit: Optional[int] = None


class SeedPositionEntry(BaseModel):
    seed_fingerprint: str
    range_id: Optional[str] = None
    mode: Optional[str] = None
    machine_id: Optional[str] = None
    machine_name: Optional[str] = None
    timestamp: Optional[str] = None
    normalized_position: Optional[float] = None
    used: bool = False
    match_found: bool = False


class SeedPositionResponse(BaseModel):
    limit: int
    seeds: List[SeedPositionEntry]


class SeedLookupNeighbor(BaseModel):
    seed_fingerprint: str
    normalized_position: Optional[float] = None
    range_id: Optional[str] = None
    timestamp: Optional[str] = None
    difference: Optional[float] = None


class SeedLookupResponse(BaseModel):
    seed_fingerprint: str
    range_id: Optional[str] = None
    mode: Optional[str] = None
    machine_id: Optional[str] = None
    machine_name: Optional[str] = None
    timestamp: Optional[str] = None
    normalized_position: Optional[float] = None
    neighbors: List[SeedLookupNeighbor] = Field(default_factory=list)



class RangeDistributionEntry(BaseModel):
    range_id: Optional[str] = None
    submissions: int
    range_value: Optional[str] = None
    submission_count: int
    submission_percent: float
    position: Optional[float] = None
    normalized_min: Optional[float] = None
    normalized_max: Optional[float] = None


class RangeDistributionResponse(BaseModel):
    slug: str
    total_submissions: int
    unique_ranges: int
    coverage_percent: float
    ranges: List[RangeDistributionEntry]
    limit: Optional[int] = None
    since: Optional[str] = None
    mode: Optional[str] = None


class MachineHealthEntry(BaseModel):
    app_instance_id: str
    last_seen: Optional[str] = None
    stale: bool


class MachineHealthResponse(BaseModel):
    stale_minutes: int
    machines: List[MachineHealthEntry]
    stale_machines: List[str]


class RecentRangeEntry(BaseModel):
    range_id: Optional[str] = None
    mode: Optional[str] = None
    app_instance_id: Optional[str] = None
    timestamp: Optional[str] = None


class RecentRangesResponse(BaseModel):
    limit: int
    ranges: List[RecentRangeEntry]


class ContributorEntry(BaseModel):
    app_instance_id: str
    submissions: int


class ContributorsResponse(BaseModel):
    limit: int
    contributors: List[ContributorEntry]


class CheckResponse(BaseModel):
    used: bool


app = FastAPI(title="AllInKeys Central Telemetry")
app.include_router(auth_ui_router)
app.include_router(pairing_ui_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(auth_ui_router)
app.include_router(pairing_router)
app.include_router(machines_router, prefix="/v1/machines")
app.include_router(machines_router, prefix="/api/machines")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)


def _expected_api_key() -> Optional[str]:
    value = os.getenv(API_KEY_ENV, "").strip()
    return value or None


# Pairing endpoints are intentionally public to allow token issuance.
# Do not secure them with API keys or auth middleware.
PUBLIC_PAIR_ENDPOINTS = {"/v1/pair/init", "/v1/pair/status", "/v1/pair/claim"}


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    if path.startswith("/v1"):
        if path.startswith("/v1/dashboard"):
            return await call_next(request)
        if path in PUBLIC_PAIR_ENDPOINTS or path.startswith("/v1/pair/"):
            return await call_next(request)
        expected = _expected_api_key()
        if expected:
            provided = request.headers.get("X-API-Key")
            if provided != expected:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Unauthorized"},
                )
    start = time.perf_counter()
    response = await call_next(request)
    if path.startswith("/v1"):
        duration_ms = (time.perf_counter() - start) * 1000
        client_host = request.client.host if request.client else "unknown"
        logger.info(
            "telemetry_request path=%s status=%s ip=%s duration_ms=%.2f",
            path,
            response.status_code,
            client_host,
            duration_ms,
        )
    return response


@app.post(
    "/auth/register",
    response_model=TokenResponse,
    tags=["Auth"],
    description="Register a new user account and return an access token.",
)
def register_user(payload: UserCreate) -> TokenResponse:
    conn = get_db_connection()
    try:
        password_hash = hash_password(payload.password)
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (payload.username, password_hash),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            ) from exc
    finally:
        conn.close()
    token = create_access_token(subject=payload.username)
    return TokenResponse(access_token=token, expires_in=TOKEN_EXPIRY * 60)


@app.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Auth"],
    description="Authenticate and receive an access token.",
)
async def login_user(request: Request) -> TokenResponse:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        username = (payload or {}).get("username")
        password = (payload or {}).get("password")
    else:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password required",
        )
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT username, password_hash
            FROM users
            WHERE username = ?
            """,
            (str(username),),
        ).fetchone()
        if not row or not verify_password(str(password), row[1]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )
    finally:
        conn.close()
    token = create_access_token(subject=str(username))
    return TokenResponse(access_token=token, expires_in=TOKEN_EXPIRY * 60)


@app.get(
    "/me",
    response_model=UserPublic,
    tags=["Auth"],
    description="Return the currently authenticated user.",
)
def get_me(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current_user


@app.get(
    "/v1/me",
    response_model=UserPublic,
    tags=["Auth"],
    description="Return the currently authenticated user.",
)
def get_me_v1(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current_user


def _safe_load_json(payload: Optional[str]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [entry for entry in parsed if isinstance(entry, dict)]
    return []


def _extract_normalized_position(payload: Optional[str]) -> Optional[float]:
    if not payload:
        return None
    entries = _safe_load_json(payload)
    for entry in reversed(entries):
        normalized = entry.get("normalized_position")
        if isinstance(normalized, (int, float)):
            normalized_value = float(normalized)
            return max(0.0, min(1.0, normalized_value))
        normalized_min = entry.get("normalized_min")
        normalized_max = entry.get("normalized_max")
        if isinstance(normalized_min, (int, float)) and isinstance(normalized_max, (int, float)):
            average = (float(normalized_min) + float(normalized_max)) / 2
            return max(0.0, min(1.0, average))
    return None


def _merge_intervals(intervals: List[tuple[float, float]]) -> List[tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _parse_since(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    relative_match = re.fullmatch(r"(\d+)([mh])", value)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        delta = timedelta(minutes=amount) if unit == "m" else timedelta(hours=amount)
        return (datetime.utcnow() - delta).isoformat() + "Z"
    if value.endswith("Z"):
        try:
            datetime.fromisoformat(value[:-1])
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid since timestamp"
            ) from exc
        return value
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid since timestamp"
        ) from exc
    return value


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1])
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@app.post(
    "/v1/seed",
    response_model=IngestResponse,
    tags=["Telemetry"],
    description="Ingest a batch of seed telemetry events.",
)
def ingest(
    items: List[TelemetryItem],
    current_user: UserPublic = Depends(get_current_user),
) -> IngestResponse:
    """Ingest seed telemetry events.

    Example:
        curl -X POST http://localhost:3088/v1/seed \\
          -H "Content-Type: application/json" \\
          -H "X-API-Key: changeme" \\
          -d '[{"seed_fingerprint":"abc123","used":true,"match_found":false}]'
    """
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Expected non-empty list body")
    conn = get_db_connection()
    try:
        with conn:
            ingest_seed_events(items, current_user=current_user, conn=conn)
    except Exception as exc:  # pragma: no cover (diagnostic trails)
        log_ingest_error(
            context="v1_seed_ingest",
            exc=exc,
            payload=[item.dict() for item in items],
            tables=["seed_events", "machines"],
            conn=conn,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest telemetry batch",
        ) from exc
    finally:
        conn.close()
    return IngestResponse(status="ok", count=len(items))


@app.get(
    "/v1/dashboard/{slug}/machines",
    response_model=MachineStatsResponse,
    tags=["Admin"],
    description="List live machine telemetry currently cached in memory.",
)
def machine_stats(
    slug: str,
    current_user: Optional[UserPublic] = Depends(get_optional_user),
) -> MachineStatsResponse:
    """Return machine telemetry for dashboard views.

    Example:
        curl -H "X-API-Key: changeme" \\
          http://localhost:3088/v1/dashboard/demo/machines
    """
    with MACHINE_REGISTRY_LOCK:
        machines = [
            entry
            for entry in MACHINE_REGISTRY.values()
            if current_user is None or entry.get("user_id") == current_user.id
        ]
    machines.sort(
        key=lambda entry: (
            entry.get("machine_name") or "",
            entry.get("machine_id") or "",
        )
    )
    window_hours = 24
    granularity = "hour"
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    window_start = now - timedelta(hours=window_hours - 1)
    filters = [
        "last_seen >= ?",
        "COALESCE(machine_id, app_instance_id) IS NOT NULL",
    ]
    params: List[Any] = [window_start.isoformat() + "Z"]
    if current_user is not None:
        filters.append("user_id = ?")
        params.append(current_user.id)
    where_clause = f"WHERE {' AND '.join(filters)}"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT
                strftime('%Y-%m-%dT%H:00:00Z', last_seen) AS bucket,
                COUNT(DISTINCT COALESCE(machine_id, app_instance_id)) AS machines
            FROM seed_events
            {where_clause}
            GROUP BY bucket
            ORDER BY bucket
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    counts = {row[0]: row[1] for row in rows if row[0]}
    series: List[MachineSeriesPoint] = []
    for offset in range(window_hours):
        bucket_time = window_start + timedelta(hours=offset)
        bucket = bucket_time.isoformat(timespec="seconds") + "Z"
        series.append(
            MachineSeriesPoint(
                bucket=bucket,
                machines=int(counts.get(bucket, 0)),
            )
        )
    return MachineStatsResponse(
        slug=slug,
        machines=machines,
        granularity=granularity,
        window=window_hours,
        series=series,
    )


@app.get(
    "/v1/dashboard/{slug}/machines/health",
    response_model=MachineHealthResponse,
    tags=["Admin"],
    description="Report machine health and stale status for dashboards.",
)
def machine_health(
    slug: str,
    stale_minutes: int = Query(60, ge=1, le=1440),
    current_user: Optional[UserPublic] = Depends(get_optional_user),
) -> MachineHealthResponse:
    del slug
    cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
    conn = get_db_connection()
    try:
        filters: List[str] = []
        params: List[Any] = []
        if current_user is not None:
            filters.append("user_id = ?")
            params.append(current_user.id)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = conn.execute(
            f"""
            SELECT id, last_seen
            FROM machines
            {where_clause}
            ORDER BY last_seen DESC
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    machines: List[MachineHealthEntry] = []
    stale_ids: List[str] = []
    for machine_id, last_seen in rows:
        last_seen_dt = _parse_iso_datetime(last_seen)
        stale = bool(last_seen_dt and last_seen_dt < cutoff)
        if last_seen_dt is None:
            stale = True
        machines.append(
            MachineHealthEntry(
                app_instance_id=machine_id,
                last_seen=last_seen,
                stale=stale,
            )
        )
        if stale:
            stale_ids.append(machine_id)
    return MachineHealthResponse(
        stale_minutes=stale_minutes,
        machines=machines,
        stale_machines=stale_ids,
    )


@app.get(
    "/v1/dashboard/{slug}/ranges/recent",
    response_model=RecentRangesResponse,
    tags=["Admin"],
    description="List recently active ranges for dashboards.",
)
def recent_ranges(
    slug: str,
    limit: int = Query(50, ge=1, le=200),
    current_user: Optional[UserPublic] = Depends(get_optional_user),
) -> RecentRangesResponse:
    del slug
    filters = ["range_id IS NOT NULL"]
    params: List[Any] = []
    if current_user is not None:
        filters.append("user_id = ?")
        params.append(current_user.id)
    params.append(limit)
    where_clause = f"WHERE {' AND '.join(filters)}"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT range_id, mode, COALESCE(machine_id, app_instance_id), last_seen
            FROM seed_events
            {where_clause}
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    ranges = [
        RecentRangeEntry(
            range_id=row[0],
            mode=row[1],
            app_instance_id=row[2],
            timestamp=row[3],
        )
        for row in rows
    ]
    return RecentRangesResponse(limit=limit, ranges=ranges)


@app.get(
    "/v1/dashboard/{slug}/contributors/top",
    response_model=ContributorsResponse,
    tags=["Admin"],
    description="Return top contributors for dashboards.",
)
def top_contributors(
    slug: str,
    limit: int = Query(20, ge=1, le=100),
    current_user: Optional[UserPublic] = Depends(get_optional_user),
) -> ContributorsResponse:
    del slug
    filters = ["COALESCE(machine_id, app_instance_id) IS NOT NULL"]
    params: List[Any] = []
    if current_user is not None:
        filters.append("user_id = ?")
        params.append(current_user.id)
    params.append(limit)
    where_clause = f"WHERE {' AND '.join(filters)}"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT COALESCE(machine_id, app_instance_id) AS app_id,
                   COUNT(*) AS submissions
            FROM seed_events
            {where_clause}
            GROUP BY app_id
            ORDER BY submissions DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    contributors = [
        ContributorEntry(app_instance_id=row[0], submissions=row[1])
        for row in rows
        if row[0]
    ]
    return ContributorsResponse(limit=limit, contributors=contributors)


@app.get(
    "/v1/seed/stats",
    response_model=SeedStatsResponse,
    tags=["Seeds"],
    description="Return aggregate seed stats with optional filters.",
)
def seed_stats(
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1),
    current_user: UserPublic = Depends(get_current_user),
) -> SeedStatsResponse:
    """Summarize seed usage totals and per-mode counts.

    Example:
        curl -H "X-API-Key: changeme" \\
          "http://localhost:3088/v1/seed/stats?since=1h"
    """
    parsed_since = _parse_since(since)
    conn = get_db_connection()
    try:
        filters = ["user_id = ?"]
        params: List[Any] = [current_user.id]
        if parsed_since:
            filters.append("last_seen >= ?")
            params.append(parsed_since)
        if mode:
            filters.append("mode = ?")
            params.append(mode)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        total_seeds = conn.execute(
            f"SELECT COUNT(*) FROM seed_events {where_clause}",
            params,
        ).fetchone()[0]
        unique_seeds = conn.execute(
            f"SELECT COUNT(DISTINCT seed_fingerprint) FROM seed_events {where_clause}",
            params,
        ).fetchone()[0]
        per_mode_rows = conn.execute(
            f"""
            SELECT mode, COUNT(*) AS count
            FROM seed_events
            {where_clause}
            GROUP BY mode
            ORDER BY count DESC
            """,
            params,
        ).fetchall()
        last_seen_row = conn.execute(
            f"SELECT MAX(last_seen) FROM seed_events {where_clause}",
            params,
        ).fetchone()
        by_mode = {row[0] or "unknown": row[1] for row in per_mode_rows}
        if limit:
            by_mode = dict(list(by_mode.items())[:limit])
        return SeedStatsResponse(
            total_seeds=total_seeds,
            unique_seed_count=unique_seeds,
            by_mode=by_mode,
            last_seen=last_seen_row[0] if last_seen_row else None,
            since=since,
            mode=mode,
        )
    finally:
        conn.close()


@app.get(
    "/v1/seed/range",
    response_model=SeedRangeResponse,
    tags=["Seeds"],
    description="Return recent range usage grouped by range_id.",
)
def seed_range(
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1),
    current_user: UserPublic = Depends(get_current_user),
) -> SeedRangeResponse:
    """Summarize range submissions by range ID.

    Example:
        curl -H "X-API-Key: changeme" \\
          "http://localhost:3088/v1/seed/range?mode=btc_only&since=24h"
    """
    parsed_since = _parse_since(since)
    conn = get_db_connection()
    try:
        filters = ["user_id = ?", "range_id IS NOT NULL"]
        params: List[Any] = [current_user.id]
        if parsed_since:
            filters.append("last_seen >= ?")
            params.append(parsed_since)
        if mode:
            filters.append("mode = ?")
            params.append(mode)
        where_clause = f"WHERE {' AND '.join(filters)}"
        limit_clause = "LIMIT ?" if limit else ""
        if limit:
            params.append(limit)
        rows = conn.execute(
            f"""
            SELECT
                range_id,
                COUNT(*) AS count,
                SUM(match_found) AS match_found,
                COUNT(DISTINCT seed_fingerprint) AS unique_seed_count
            FROM seed_events
            {where_clause}
            GROUP BY range_id
            ORDER BY count DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
        ranges = [
            {
                "range_id": row[0],
                "count": row[1],
                "match_found": row[2] or 0,
                "unique_seed_count": row[3],
            }
            for row in rows
        ]
        return SeedRangeResponse(
            ranges=ranges,
            since=since,
            mode=mode,
            limit=limit,
        )
    finally:
        conn.close()


@app.get(
    "/v1/seed/positions",
    response_model=SeedPositionResponse,
    tags=["Seeds"],
    description="Return recent seed events with normalized positions for visualization.",
)
def seed_positions(
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    range_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    current_user: UserPublic = Depends(get_current_user),
) -> SeedPositionResponse:
    parsed_since = _parse_since(since)
    filters = ["user_id = ?", "range_recent IS NOT NULL"]
    params: List[Any] = [current_user.id]
    if mode:
        filters.append("mode = ?")
        params.append(mode)
    if range_id:
        filters.append("range_id = ?")
        params.append(range_id)
    if parsed_since:
        filters.append("last_seen >= ?")
        params.append(parsed_since)
    where_clause = f"WHERE {' AND '.join(filters)}"
    limit_value = max(1, min(limit, 500))
    params.append(limit_value)

    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT seed_fingerprint, range_id, mode, machine_id, machine_name, last_seen,
                   used, match_found, range_recent
            FROM seed_events
            {where_clause}
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    seeds = [
        SeedPositionEntry(
            seed_fingerprint=row[0],
            range_id=row[1],
            mode=row[2],
            machine_id=row[3],
            machine_name=row[4],
            timestamp=row[5],
            used=bool(row[6]),
            match_found=bool(row[7]),
            normalized_position=_extract_normalized_position(row[8]),
        )
        for row in rows
    ]
    return SeedPositionResponse(limit=limit_value, seeds=seeds)


@app.get(
    "/v1/seed/lookup",
    response_model=SeedLookupResponse,
    tags=["Seeds"],
    description="Look up a specific seed fingerprint and nearby submissions based on range position.",
)
def seed_lookup(
    seed_fingerprint: str = Query(...),
    limit: int = Query(5, ge=1, le=50),
    since: Optional[str] = Query(None),
    current_user: UserPublic = Depends(get_current_user),
) -> SeedLookupResponse:
    parsed_since = _parse_since(since)
    conn = get_db_connection()
    try:
        target_row = conn.execute(
            """
            SELECT seed_fingerprint, range_id, mode, machine_id, machine_name,
                   last_seen, range_recent
            FROM seed_events
            WHERE seed_fingerprint = ? AND user_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (seed_fingerprint, current_user.id),
        ).fetchone()
        if not target_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Seed not found",
            )
        normalized_target = _extract_normalized_position(target_row[6])

        neighbor_filters = ["user_id = ?", "range_recent IS NOT NULL"]
        neighbor_params: List[Any] = [current_user.id]
        if parsed_since:
            neighbor_filters.append("last_seen >= ?")
            neighbor_params.append(parsed_since)
        where_clause = f"WHERE {' AND '.join(neighbor_filters)}"
        neighbor_limit = min(max(limit * 25, 50), 1000)
        neighbor_rows = conn.execute(
            f"""
            SELECT seed_fingerprint, range_id, last_seen, range_recent
            FROM seed_events
            {where_clause} AND seed_fingerprint != ?
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (*neighbor_params, seed_fingerprint, neighbor_limit),
        ).fetchall()
    finally:
        conn.close()

    neighbors: List[SeedLookupNeighbor]
    if normalized_target is not None:
        candidates = []
        for row in neighbor_rows:
            entry_pos = _extract_normalized_position(row[3])
            if entry_pos is None:
                continue
            diff = abs(entry_pos - normalized_target)
            candidates.append((diff, row, entry_pos))
        candidates.sort(key=lambda item: (item[0],))
        selected = candidates[:limit]
        neighbors = [
            SeedLookupNeighbor(
                seed_fingerprint=row[0],
                range_id=row[1],
                timestamp=row[2],
                normalized_position=entry_pos,
                difference=round(diff * 100, 2),
            )
            for diff, row, entry_pos in selected
        ]
    else:
        neighbors = [
            SeedLookupNeighbor(
                seed_fingerprint=row[0],
                range_id=row[1],
                timestamp=row[2],
                normalized_position=_extract_normalized_position(row[3]),
                difference=None,
            )
            for row in neighbor_rows[:limit]
        ]

    return SeedLookupResponse(
        seed_fingerprint=target_row[0],
        range_id=target_row[1],
        mode=target_row[2],
        machine_id=target_row[3],
        machine_name=target_row[4],
        timestamp=target_row[5],
        normalized_position=normalized_target,
        neighbors=neighbors,
    )


@app.get(
    "/v1/dashboard/{slug}/ranges/distribution",
    response_model=RangeDistributionResponse,
    tags=["Admin"],
    description="Compute range distribution coverage for dashboards.",
)
def range_distribution(
    slug: str,
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    current_user: Optional[UserPublic] = Depends(get_optional_user),
) -> RangeDistributionResponse:
    """Aggregate distribution metrics for range coverage charts.

    Example:
        curl -H "X-API-Key: changeme" \\
          "http://localhost:3088/v1/dashboard/demo/ranges/distribution"
    """
    parsed_since = _parse_since(since)
    conn = get_db_connection()
    try:
        filters = ["se.range_id IS NOT NULL"]
        params: List[Any] = []
        if current_user is not None:
            filters.append("se.user_id = ?")
            params.append(current_user.id)
        if parsed_since:
            filters.append("se.last_seen >= ?")
            params.append(parsed_since)
        if mode:
            filters.append("se.mode = ?")
            params.append(mode)
        where_clause = f"WHERE {' AND '.join(filters)}"
        counts = conn.execute(
            f"""
            SELECT se.range_id, COUNT(*) AS count
            FROM seed_events AS se
            {where_clause}
            GROUP BY se.range_id
            """,
            params,
        ).fetchall()
        total_submissions = sum(row[1] for row in counts) if counts else 0
        counts = sorted(counts, key=lambda row: row[1], reverse=True)
        if limit:
            counts = counts[:limit]
        distribution_rows = conn.execute(
            f"""
            SELECT se.range_distribution
            FROM seed_events AS se
            {where_clause} AND se.range_distribution IS NOT NULL
            """,
            params,
        ).fetchall()
        position_map: Dict[str, Dict[str, Optional[float]]] = {}
        for row in distribution_rows:
            for entry in _safe_load_json(row[0]):
                range_key = entry.get("range_id") or "default"
                normalized_min = entry.get("normalized_min")
                normalized_max = entry.get("normalized_max")
                if not isinstance(normalized_min, (int, float)) or not isinstance(
                    normalized_max, (int, float)
                ):
                    continue
                summary = position_map.setdefault(
                    range_key, {"normalized_min": None, "normalized_max": None}
                )
                summary["normalized_min"] = (
                    normalized_min
                    if summary["normalized_min"] is None
                    else min(summary["normalized_min"], normalized_min)
                )
                summary["normalized_max"] = (
                    normalized_max
                    if summary["normalized_max"] is None
                    else max(summary["normalized_max"], normalized_max)
                )
        ranges: List[Dict[str, Any]] = []
        for range_id, count in counts:
            position_summary = position_map.get(range_id)
            normalized_min = (
                position_summary.get("normalized_min")
                if position_summary
                else None
            )
            normalized_max = (
                position_summary.get("normalized_max")
                if position_summary
                else None
            )
            position = None
            if normalized_min is not None and normalized_max is not None:
                position = (normalized_min + normalized_max) / 2 * 100
            percent = (count / total_submissions * 100) if total_submissions else 0
            ranges.append(
                {
                    "range_id": range_id,
                    "submissions": count,
                    "range_value": range_id,
                    "submission_count": count,
                    "submission_percent": percent,
                    "position": position,
                    "normalized_min": normalized_min,
                    "normalized_max": normalized_max,
                }
            )
        intervals = [
            (entry["normalized_min"], entry["normalized_max"])
            for entry in ranges
            if entry["normalized_min"] is not None
            and entry["normalized_max"] is not None
        ]
        merged = _merge_intervals(intervals)
        coverage = sum(end - start for start, end in merged) * 100 if merged else 0
        return RangeDistributionResponse(
            slug=slug,
            total_submissions=total_submissions,
            unique_ranges=len(ranges),
            coverage_percent=coverage,
            ranges=ranges,
            limit=limit,
            since=since,
            mode=mode,
        )
    finally:
        conn.close()


@app.get(
    "/v1/seed/check",
    response_model=CheckResponse,
    tags=["Seeds"],
    description="Check if a seed fingerprint has been seen before.",
)
def check_get(
    seed_fingerprint: str = Query(...),
    mode: Optional[str] = None,
    range_id: Optional[str] = None,
    current_user: UserPublic = Depends(get_current_user),
) -> CheckResponse:
    """Check whether a seed fingerprint exists in the telemetry store.

    Example:
        curl -H "X-API-Key: changeme" \\
          "http://localhost:3088/v1/seed/check?seed_fingerprint=abc123"
    """
    return _check(seed_fingerprint, current_user.id)


class CheckBody(BaseModel):
    seed_fingerprint: str
    mode: Optional[str] = None
    range_id: Optional[str] = None


@app.post(
    "/v1/seed/check",
    response_model=CheckResponse,
    tags=["Seeds"],
    description="Check if a seed fingerprint has been seen before via POST.",
)
def check_post(
    body: CheckBody, current_user: UserPublic = Depends(get_current_user)
) -> CheckResponse:
    """Check whether a seed fingerprint exists in the telemetry store.

    Example:
        curl -X POST http://localhost:3088/v1/seed/check \\
          -H "Content-Type: application/json" \\
          -H "X-API-Key: changeme" \\
          -d '{"seed_fingerprint":"abc123"}'
    """
    return _check(body.seed_fingerprint, current_user.id)


def _check(seed_fp: str, user_id: int) -> CheckResponse:
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM seed_events WHERE seed_fingerprint=? AND user_id=? LIMIT 1",
            (seed_fp, user_id),
        )
        used = cur.fetchone() is not None
        return CheckResponse(used=bool(used))
    finally:
        conn.close()


def _mount_dashboard_ui() -> None:
    dashboard_dist = os.path.join(
        os.path.dirname(__file__), "..", "telemetry_dashboard", "dist"
    )
    if os.path.isdir(dashboard_dist):
        app.mount(
            "/",
            StaticFiles(directory=dashboard_dist, html=True),
            name="dashboard",
        )


_mount_dashboard_ui()
