from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from config.telemetry import TOKEN_EXPIRY
from telemetry_service.auth import create_access_token, hash_password, verify_password
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_current_user
from telemetry_service.models import TokenResponse, UserCreate, UserPublic

API_KEY_ENV = "TELEMETRY_API_KEY"
logger = logging.getLogger("telemetry")
logging.basicConfig(level=os.getenv("TELEMETRY_LOG_LEVEL", "INFO"))


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


class MachineStatsResponse(BaseModel):
    slug: str
    machines: List[MachineInfo]


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


class RangeDistributionEntry(BaseModel):
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
    since: Optional[str] = None
    mode: Optional[str] = None


class CheckResponse(BaseModel):
    used: bool


app = FastAPI(title="AllInKeys Central Telemetry")
MACHINE_REGISTRY: Dict[tuple[int, str], Dict[str, Any]] = {}
MACHINE_REGISTRY_LOCK = threading.Lock()


def _expected_api_key() -> Optional[str]:
    value = os.getenv(API_KEY_ENV, "").strip()
    return value or None


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    if path.startswith("/v1"):
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
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT username, password_hash
            FROM users
            WHERE username = ?
            """,
            (form_data.username,),
        ).fetchone()
        if not row or not verify_password(form_data.password, row[1]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )
    finally:
        conn.close()
    token = create_access_token(subject=form_data.username)
    return TokenResponse(access_token=token, expires_in=TOKEN_EXPIRY * 60)


@app.get(
    "/me",
    response_model=UserPublic,
    tags=["Auth"],
    description="Return the currently authenticated user.",
)
def get_me(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
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
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_db_connection()
    try:
        with conn:
            for item in items:
                ts = item.timestamp_iso or now
                machine_key = item.machine_id or item.app_instance_id
                machine_name = item.machine_name
                if machine_key:
                    with MACHINE_REGISTRY_LOCK:
                        registry_key = (current_user.id, machine_key)
                        existing = MACHINE_REGISTRY.get(registry_key, {})
                        machine_name = (
                            item.machine_name
                            or existing.get("machine_name")
                            or machine_key
                        )
                        MACHINE_REGISTRY[registry_key] = {
                            "user_id": current_user.id,
                            "machine_id": machine_key,
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
                            user_id, machine_name, gpu_info, status, last_seen
                        ) VALUES (?, ?, ?, 'online', ?)
                        ON CONFLICT(user_id, machine_name) DO UPDATE SET
                            gpu_info=COALESCE(excluded.gpu_info, machines.gpu_info),
                            status='online',
                            last_seen=excluded.last_seen
                        """,
                        (current_user.id, machine_name, item.gpu_name, ts),
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
                        item.machine_id,
                        machine_name,
                        json.dumps(item.range_recent) if item.range_recent else None,
                        json.dumps(item.range_distribution)
                        if item.range_distribution
                        else None,
                        json.dumps(item.reference_overlays)
                        if item.reference_overlays
                        else None,
                    ),
                )
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
    current_user: UserPublic = Depends(get_current_user),
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
            if entry.get("user_id") == current_user.id
        ]
    machines.sort(
        key=lambda entry: (
            entry.get("machine_name") or "",
            entry.get("machine_id") or "",
        )
    )
    return MachineStatsResponse(slug=slug, machines=machines)


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
    "/v1/dashboard/{slug}/ranges/distribution",
    response_model=RangeDistributionResponse,
    tags=["Admin"],
    description="Compute range distribution coverage for dashboards.",
)
def range_distribution(
    slug: str,
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    current_user: UserPublic = Depends(get_current_user),
) -> RangeDistributionResponse:
    """Aggregate distribution metrics for range coverage charts.

    Example:
        curl -H "X-API-Key: changeme" \\
          "http://localhost:3088/v1/dashboard/demo/ranges/distribution"
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
        counts = conn.execute(
            f"""
            SELECT range_id, COUNT(*) AS count
            FROM seed_events
            {where_clause}
            GROUP BY range_id
            """,
            params,
        ).fetchall()
        total_submissions = sum(row[1] for row in counts) if counts else 0
        distribution_rows = conn.execute(
            f"""
            SELECT range_distribution
            FROM seed_events
            {where_clause} AND range_distribution IS NOT NULL
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
