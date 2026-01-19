from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_current_admin_user
from telemetry_service.models import (
    AdminKeyspaceProgress,
    AdminMachineSummary,
    AdminUserSummary,
    TimeSeriesPoint,
    TimeSeriesResponse,
    UserPublic,
)
from telemetry_service.routes.machines import _status_from_last_seen

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="/admin", tags=["Admin"])


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


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> List[tuple[float, float]]:
    sorted_intervals = sorted(intervals)
    if not sorted_intervals:
        return []
    merged = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _intervals_from_rows(rows: Iterable[tuple[str]]) -> List[tuple[float, float]]:
    intervals: List[tuple[float, float]] = []
    for (payload,) in rows:
        for entry in _safe_load_json(payload):
            normalized_min = entry.get("normalized_min")
            normalized_max = entry.get("normalized_max")
            if isinstance(normalized_min, (int, float)) and isinstance(
                normalized_max, (int, float)
            ):
                intervals.append((float(normalized_min), float(normalized_max)))
    return intervals


def _intervals_from_payload(payload: Optional[str]) -> List[tuple[float, float]]:
    return _intervals_from_rows([(payload or "",)])


def _coverage_from_rows(rows: Iterable[tuple[str]]) -> float:
    intervals = _intervals_from_rows(rows)
    merged = _merge_intervals(intervals)
    return sum(end - start for start, end in merged) * 100 if merged else 0


RELATIVE_RE = re.compile(r"(\d+)([mh])")


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    relative_match = RELATIVE_RE.fullmatch(value)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        delta = timedelta(minutes=amount) if unit == "m" else timedelta(hours=amount)
        return datetime.utcnow() - delta
    if value.endswith("Z"):
        value = value[:-1]
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_since_required(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = _parse_since(value)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid since timestamp",
        )
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _bucket_index(timestamp: datetime, start: datetime, bucket_seconds: int) -> int:
    return int((timestamp - start).total_seconds() // bucket_seconds)


def _build_time_buckets(
    start: datetime, end: datetime, bucket_minutes: int
) -> List[datetime]:
    bucket_seconds = bucket_minutes * 60
    total_seconds = max(0, (end - start).total_seconds())
    bucket_count = int(total_seconds // bucket_seconds) + 1
    return [
        start + timedelta(seconds=bucket_seconds * idx) for idx in range(bucket_count)
    ]


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    current_user: UserPublic = Depends(get_current_admin_user),
) -> HTMLResponse:
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1]
    elif request.cookies.get("telemetry_session"):
        token = request.cookies.get("telemetry_session", "")
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "current_user": current_user,
            "auth_token": token,
        },
    )


@router.get("/users/summary", response_model=List[AdminUserSummary])
def admin_users_summary(
    current_user: UserPublic = Depends(get_current_admin_user),
) -> List[AdminUserSummary]:
    now = datetime.utcnow()
    cutoff = (now - timedelta(seconds=60)).isoformat() + "Z"
    conn = get_db_connection()
    try:
        users = conn.execute(
            """
            SELECT id, username
            FROM users
            ORDER BY username
            """
        ).fetchall()
        summaries: List[AdminUserSummary] = []
        for user_id, username in users:
            machine_count = conn.execute(
                "SELECT COUNT(*) FROM machines WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            kps_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM seed_events
                WHERE user_id = ? AND last_seen >= ?
                """,
                (user_id, cutoff),
            ).fetchone()[0]
            total_kps = kps_count / 60 if kps_count else 0
            avg_kps = total_kps / machine_count if machine_count else 0
            coverage_rows = conn.execute(
                """
                SELECT range_distribution
                FROM seed_events
                WHERE user_id = ? AND range_distribution IS NOT NULL
                """,
                (user_id,),
            ).fetchall()
            coverage_percent = _coverage_from_rows(coverage_rows)
            summaries.append(
                AdminUserSummary(
                    id=user_id,
                    username=username,
                    machine_count=machine_count,
                    avg_kps=round(avg_kps, 2),
                    coverage_percent=round(coverage_percent, 2),
                )
            )
        return summaries
    finally:
        conn.close()


@router.get("/machines/summary", response_model=List[AdminMachineSummary])
def admin_machines_summary(
    current_user: UserPublic = Depends(get_current_admin_user),
) -> List[AdminMachineSummary]:
    now = datetime.utcnow()
    cutoff = (now - timedelta(seconds=60)).isoformat() + "Z"
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT machines.id,
                   machines.machine_name,
                   machines.user_id,
                   users.username,
                   machines.gpu_info,
                   machines.version,
                   machines.last_seen
            FROM machines
            JOIN users ON users.id = machines.user_id
            ORDER BY users.username, machines.machine_name, machines.id
            """
        ).fetchall()
        response: List[AdminMachineSummary] = []
        for row in rows:
            machine_id, machine_name, user_id, username, gpu_info, version, last_seen = (
                row
            )
            kps_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM seed_events
                WHERE machine_id = ? AND last_seen >= ?
                """,
                (machine_id, cutoff),
            ).fetchone()[0]
            response.append(
                AdminMachineSummary(
                    id=machine_id,
                    machine_name=machine_name or machine_id,
                    user_id=user_id,
                    username=username,
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


@router.get("/keyspace/progress", response_model=AdminKeyspaceProgress)
def admin_keyspace_progress(
    since: Optional[str] = Query(None),
    current_user: UserPublic = Depends(get_current_admin_user),
) -> AdminKeyspaceProgress:
    parsed_since = _parse_since_required(since)
    since_value = _format_timestamp(parsed_since) if parsed_since else None
    conn = get_db_connection()
    try:
        filters = []
        params: List[Any] = []
        if parsed_since:
            filters.append("last_seen >= ?")
            params.append(since_value)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        range_where = f"{where_clause} AND range_id IS NOT NULL" if where_clause else "WHERE range_id IS NOT NULL"
        total_ranges = conn.execute(
            f"""
            SELECT COUNT(DISTINCT range_id)
            FROM seed_events
            {range_where}
            """,
            params,
        ).fetchone()[0]
        total_submissions = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM seed_events
            {where_clause}
            """,
            params,
        ).fetchone()[0]
        unique_seed_count = conn.execute(
            f"""
            SELECT COUNT(DISTINCT seed_fingerprint)
            FROM seed_events
            {where_clause}
            """,
            params,
        ).fetchone()[0]
        coverage_rows = conn.execute(
            f"""
            SELECT range_distribution
            FROM seed_events
            {where_clause} {"AND" if where_clause else "WHERE"} range_distribution IS NOT NULL
            """,
            params,
        ).fetchall()
        coverage_percent = _coverage_from_rows(coverage_rows)
        window_start = (
            conn.execute(
                f"""
                SELECT MIN(last_seen)
                FROM seed_events
                {where_clause}
                """,
                params,
            ).fetchone()[0]
            if total_submissions
            else None
        )
        window_end = (
            conn.execute(
                f"""
                SELECT MAX(last_seen)
                FROM seed_events
                {where_clause}
                """,
                params,
            ).fetchone()[0]
            if total_submissions
            else None
        )
        return AdminKeyspaceProgress(
            total_ranges=total_ranges or 0,
            total_submissions=total_submissions or 0,
            unique_seed_count=unique_seed_count or 0,
            coverage_percent=round(coverage_percent, 2),
            window_start=window_start,
            window_end=window_end,
        )
    finally:
        conn.close()


def _load_time_window(
    since: Optional[str], bucket_minutes: int
) -> tuple[datetime, datetime, int]:
    now = datetime.utcnow()
    parsed_since = _parse_since_required(since)
    start = parsed_since if parsed_since else now - timedelta(hours=24)
    start = start.replace(microsecond=0)
    bucket_minutes = max(1, bucket_minutes)
    return start, now.replace(microsecond=0), bucket_minutes


def _series_from_counts(
    buckets: List[datetime],
    counts: List[int],
    bucket_minutes: int,
    per_second: bool = False,
) -> List[TimeSeriesPoint]:
    bucket_seconds = bucket_minutes * 60
    points: List[TimeSeriesPoint] = []
    for bucket, count in zip(buckets, counts):
        value = count / bucket_seconds if per_second else float(count)
        points.append(TimeSeriesPoint(timestamp=_format_timestamp(bucket), value=value))
    return points


@router.get("/timeseries/kps", response_model=TimeSeriesResponse)
def admin_timeseries_kps(
    since: Optional[str] = Query(None),
    bucket_minutes: int = Query(15, ge=1, le=1440),
    current_user: UserPublic = Depends(get_current_admin_user),
) -> TimeSeriesResponse:
    start, end, bucket_minutes = _load_time_window(since, bucket_minutes)
    buckets = _build_time_buckets(start, end, bucket_minutes)
    counts = [0 for _ in buckets]
    bucket_seconds = bucket_minutes * 60
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT last_seen
            FROM seed_events
            WHERE last_seen >= ? AND last_seen <= ?
            """,
            (_format_timestamp(start), _format_timestamp(end)),
        ).fetchall()
    finally:
        conn.close()
    for (last_seen,) in rows:
        if not last_seen:
            continue
        timestamp = _parse_since(last_seen)
        if not timestamp:
            continue
        index = _bucket_index(timestamp, start, bucket_seconds)
        if 0 <= index < len(counts):
            counts[index] += 1
    points = _series_from_counts(buckets, counts, bucket_minutes, per_second=True)
    return TimeSeriesResponse(metric="kps", bucket_minutes=bucket_minutes, points=points)


@router.get("/timeseries/backlog", response_model=TimeSeriesResponse)
def admin_timeseries_backlog(
    since: Optional[str] = Query(None),
    bucket_minutes: int = Query(15, ge=1, le=1440),
    current_user: UserPublic = Depends(get_current_admin_user),
) -> TimeSeriesResponse:
    start, end, bucket_minutes = _load_time_window(since, bucket_minutes)
    buckets = _build_time_buckets(start, end, bucket_minutes)
    counts = [0 for _ in buckets]
    bucket_seconds = bucket_minutes * 60
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT last_seen
            FROM seed_events
            WHERE used = 0 AND last_seen >= ? AND last_seen <= ?
            """,
            (_format_timestamp(start), _format_timestamp(end)),
        ).fetchall()
    finally:
        conn.close()
    for (last_seen,) in rows:
        if not last_seen:
            continue
        timestamp = _parse_since(last_seen)
        if not timestamp:
            continue
        index = _bucket_index(timestamp, start, bucket_seconds)
        if 0 <= index < len(counts):
            counts[index] += 1
    points = _series_from_counts(buckets, counts, bucket_minutes, per_second=False)
    return TimeSeriesResponse(
        metric="backlog", bucket_minutes=bucket_minutes, points=points
    )


@router.get("/timeseries/coverage", response_model=TimeSeriesResponse)
def admin_timeseries_coverage(
    since: Optional[str] = Query(None),
    bucket_minutes: int = Query(60, ge=1, le=1440),
    current_user: UserPublic = Depends(get_current_admin_user),
) -> TimeSeriesResponse:
    start, end, bucket_minutes = _load_time_window(since, bucket_minutes)
    buckets = _build_time_buckets(start, end, bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT last_seen, range_distribution
            FROM seed_events
            WHERE range_distribution IS NOT NULL AND last_seen >= ? AND last_seen <= ?
            ORDER BY last_seen
            """,
            (_format_timestamp(start), _format_timestamp(end)),
        ).fetchall()
    finally:
        conn.close()
    intervals: List[tuple[float, float]] = []
    points: List[TimeSeriesPoint] = []
    row_index = 0
    for bucket_start in buckets:
        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
        while row_index < len(rows):
            last_seen, payload = rows[row_index]
            timestamp = _parse_since(last_seen)
            if not timestamp:
                row_index += 1
                continue
            if timestamp >= bucket_end:
                break
            intervals.extend(_intervals_from_payload(payload))
            row_index += 1
        merged = _merge_intervals(intervals)
        coverage_value = sum(end - start for start, end in merged) * 100 if merged else 0
        points.append(
            TimeSeriesPoint(
                timestamp=_format_timestamp(bucket_start),
                value=round(coverage_value, 2),
            )
        )
    return TimeSeriesResponse(
        metric="coverage", bucket_minutes=bucket_minutes, points=points
    )
