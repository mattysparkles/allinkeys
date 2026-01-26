from __future__ import annotations

import json
import sqlite3
import traceback
from datetime import datetime

from pathlib import Path
from typing import Any, Iterable, Sequence

from config.directories import LOG_DIR

ERROR_LOG_PATH = Path(LOG_DIR) / "telemetry_ingest_errors.log"
SNAPSHOT_LOG_PATH = Path(LOG_DIR) / "telemetry_snapshot_payloads.log"


def _ensure_log_dir(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _collect_schema(conn: sqlite3.Connection, table: str) -> Any:
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        rows = cursor.fetchall()
        schema = []
        for row in rows:
            schema.append(
                {
                    "cid": row[0],
                    "name": row[1],
                    "type": row[2],
                    "notnull": bool(row[3]),
                    "default_value": row[4],
                    "pk": bool(row[5]),
                }
            )
        return schema
    except Exception as exc:  # pragma: no cover (best effort logging)
        return {"error": str(exc)}


def log_ingest_error(
    *,
    context: str,
    exc: Exception,
    payload: Any = None,
    tables: Iterable[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist diagnostics for telemetry ingest failures."""

    _ensure_log_dir(ERROR_LOG_PATH)
    payload_json = None
    try:
        payload_json = json.dumps(payload, default=str, ensure_ascii=False, indent=2)
    except Exception:
        try:
            payload_json = str(payload)
        except Exception:
            payload_json = "<unserializable payload>"
    schema_info: dict[str, Any] = {}
    if conn and tables:
        for table in tables:
            schema_info[table] = _collect_schema(conn, table)
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "context": context,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "payload": payload_json,
        "schema": schema_info,
    }
    try:
        with ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")
    except Exception:
        pass


def log_raw_snapshot(*, payload: str, machine_id: str | None = None) -> None:
    """Durably capture the raw JSON sent by machines for later inspection."""

    _ensure_log_dir(SNAPSHOT_LOG_PATH)
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "machine_id": machine_id,
        "payload": payload,
    }
    try:
        with SNAPSHOT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")
    except Exception:
        pass
