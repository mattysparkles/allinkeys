from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


DB_PATH = os.getenv("CENTRAL_TELEMETRY_DB", os.path.abspath(os.path.join(os.path.dirname(__file__), "../logs/central_telemetry.db")))


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seed_fingerprint TEXT NOT NULL,
            app_instance_id TEXT,
            client_version TEXT,
            mode TEXT,
            range_id TEXT,
            first_seen TEXT,
            last_seen TEXT,
            used INTEGER DEFAULT 0,
            match_found INTEGER DEFAULT 0
        );
        """
    )
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(seed_events)").fetchall()
    }
    optional_columns = {
        "machine_id": "TEXT",
        "machine_name": "TEXT",
        "range_recent": "TEXT",
        "range_distribution": "TEXT",
        "reference_overlays": "TEXT",
    }
    for name, col_type in optional_columns.items():
        if name not in existing_cols:
            conn.execute(f"ALTER TABLE seed_events ADD COLUMN {name} {col_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seed_fingerprint ON seed_events(seed_fingerprint)"
    )
    has_unique_index = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='index' AND name='uniq_seed_fingerprint_range_id'
        """
    ).fetchone()
    if not has_unique_index:
        conn.execute(
            """
            DELETE FROM seed_events
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM seed_events
                GROUP BY seed_fingerprint, range_id
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_seed_fingerprint_range_id
            ON seed_events(seed_fingerprint, range_id)
            """
        )
    return conn


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
    range_recent: Optional[List[Dict[str, Any]]] = None
    range_distribution: Optional[List[Dict[str, Any]]] = None
    reference_overlays: Optional[List[Dict[str, Any]]] = None


app = FastAPI(title="AllInKeys Central Telemetry")


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


@app.post("/v1/seed")
def ingest(items: List[TelemetryItem]) -> Dict[str, Any]:
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Expected non-empty list body")
    now = datetime.utcnow().isoformat() + "Z"
    conn = _connect()
    try:
        with conn:
            for item in items:
                ts = item.timestamp_iso or now
                conn.execute(
                    """
                    INSERT INTO seed_events (
                        seed_fingerprint, app_instance_id, client_version, mode, range_id,
                        first_seen, last_seen, used, match_found, machine_id, machine_name,
                        range_recent, range_distribution, reference_overlays
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(seed_fingerprint, range_id) DO UPDATE SET
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
                        item.machine_name,
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
    return {"status": "ok", "count": len(items)}


@app.get("/v1/seed/stats")
def seed_stats(
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1),
) -> Dict[str, Any]:
    conn = _connect()
    try:
        filters = []
        params: List[Any] = []
        if since:
            filters.append("last_seen >= ?")
            params.append(since)
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
        per_mode = [{"mode": row[0], "count": row[1]} for row in per_mode_rows]
        if limit:
            per_mode = per_mode[:limit]
        return {
            "total_seeds": total_seeds,
            "unique_seeds": unique_seeds,
            "per_mode": per_mode,
            "since": since,
            "mode": mode,
        }
    finally:
        conn.close()


@app.get("/v1/seed/range")
def seed_range(
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1),
) -> Dict[str, Any]:
    conn = _connect()
    try:
        filters = ["range_id IS NOT NULL"]
        params: List[Any] = []
        if since:
            filters.append("last_seen >= ?")
            params.append(since)
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
                MAX(last_seen) AS last_seen,
                MAX(used) AS used
            FROM seed_events
            {where_clause}
            GROUP BY range_id
            ORDER BY last_seen DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
        ranges = [
            {
                "range_id": row[0],
                "count": row[1],
                "last_seen": row[2],
                "used": bool(row[3]),
            }
            for row in rows
        ]
        return {"ranges": ranges, "since": since, "mode": mode, "limit": limit}
    finally:
        conn.close()


@app.get("/v1/dashboard/{slug}/ranges/distribution")
def range_distribution(
    slug: str,
    since: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
) -> Dict[str, Any]:
    conn = _connect()
    try:
        filters = ["range_id IS NOT NULL"]
        params: List[Any] = []
        if since:
            filters.append("last_seen >= ?")
            params.append(since)
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
        return {
            "slug": slug,
            "total_submissions": total_submissions,
            "unique_ranges": len(ranges),
            "coverage_percent": coverage,
            "ranges": ranges,
            "since": since,
            "mode": mode,
        }
    finally:
        conn.close()


@app.get("/v1/seed/check")
def check_get(
    seed_fingerprint: str = Query(...),
    mode: Optional[str] = None,
    range_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _check(seed_fingerprint)


class CheckBody(BaseModel):
    seed_fingerprint: str
    mode: Optional[str] = None
    range_id: Optional[str] = None


@app.post("/v1/seed/check")
def check_post(body: CheckBody) -> Dict[str, Any]:
    return _check(body.seed_fingerprint)


def _check(seed_fp: str) -> Dict[str, Any]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT 1 FROM seed_events WHERE seed_fingerprint=? LIMIT 1",
            (seed_fp,),
        )
        used = cur.fetchone() is not None
        return {"used": bool(used)}
    finally:
        conn.close()
