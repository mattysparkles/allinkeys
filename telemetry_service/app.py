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
