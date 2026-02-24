#!/usr/bin/env python3
"""Lightweight telemetry diagnostics for local AllInKeys client installs.

This script intentionally avoids importing heavy runtime modules that can
initialize multiprocessing queues as side effects.
"""

from __future__ import annotations

import argparse
import ast
import os
import sqlite3
import sys
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.directories as directories
import config.telemetry as telemetry_config


def _print(label: str, value) -> None:
    print(f"{label}: {value}", flush=True)


def _function_has_param(path: Path, function_name: str, param: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            all_args = list(node.args.args) + list(node.args.kwonlyargs)
            return any(arg.arg == param for arg in all_args)
    return False


def _telemetry_opted_out(local_config_path: Path) -> bool:
    if not local_config_path.exists():
        return False
    try:
        data = json.loads(local_config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("telemetry_disabled", False))


def _auth_token_present(token_path: Path) -> bool:
    if os.getenv("AUTH_TOKEN"):
        return True
    if not token_path.exists():
        return False
    try:
        return bool(token_path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _recent_telemetry_log_lines(limit: int = 80) -> list[str]:
    log_dir = Path(directories.LOG_DIR)
    if not log_dir.exists():
        return []
    matches: deque[str] = deque(maxlen=limit)
    for path in sorted(log_dir.rglob("*.log")):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "[Telemetry]" in line:
                        matches.append(f"{path.name}: {line.rstrip()}")
        except OSError:
            continue
    return list(matches)


def _queue_db_event_count(path: Path) -> str:
    try:
        with sqlite3.connect(path) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            if "telemetry" not in tables:
                return "telemetry table missing"
            queued = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
            return str(int(queued))
    except Exception as exc:  # pragma: no cover - diagnostic path
        return f"error: {exc!r}"


def _fmt_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _emit_test_event() -> str:
    try:
        import time
        import secrets
        import core.keygen as keygen

        seed = secrets.randbits(255) | (1 << 128)
        mode, default_range_id = keygen._telemetry_context()
        keygen._emit_seed_event(
            seed,
            mode=mode,
            range_id=default_range_id,
            used=False,
            match_found=False,
        )
        return "ok"
    except Exception as exc:  # pragma: no cover - runtime diagnostic path
        return f"error: {exc!r}"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--emit-test", action="store_true")
    args, _ = parser.parse_known_args()

    keygen_path = ROOT / "core" / "keygen.py"
    telemetry_path = ROOT / "core" / "telemetry.py"
    token_path = ROOT / "config" / ".telemetry_token"
    local_config_path = ROOT / "config" / "local_telemetry.json"

    _print("keygen_path", keygen_path)
    _print("telemetry_path", telemetry_path)
    _print(
        "keygen_has_emit_seed_event_helper",
        "_emit_seed_event(" in keygen_path.read_text(encoding="utf-8"),
    )
    _print(
        "record_seed_event_has_range_observation",
        _function_has_param(telemetry_path, "record_seed_event", "range_observation"),
    )
    _print("SEED_TELEMETRY_ENABLED", telemetry_config.SEED_TELEMETRY_ENABLED)
    _print("TELEMETRY_ENDPOINT", telemetry_config.TELEMETRY_ENDPOINT)
    _print("TELEMETRY_CHECK_ENDPOINT", telemetry_config.TELEMETRY_CHECK_ENDPOINT)
    _print("telemetry_opted_out", _telemetry_opted_out(local_config_path))
    _print("auth_token_present", _auth_token_present(token_path))

    queue_db_paths = sorted(Path(directories.LOG_DIR).glob("telemetry_queue*.db"))
    _print("queue_db_count", len(queue_db_paths))
    for path in queue_db_paths:
        _print("queue_db", path)
        _print("queue_db_mtime_utc", _fmt_mtime(path))
        _print("queued_events", _queue_db_event_count(path))

    if args.emit_test:
        _print("emit_test_event", _emit_test_event())
        queue_db_paths = sorted(Path(directories.LOG_DIR).glob("telemetry_queue*.db"))
        for path in queue_db_paths:
            _print("post_emit_queue_db", path)
            _print("post_emit_queued_events", _queue_db_event_count(path))

    print("recent_telemetry_log_lines:", flush=True)
    for line in _recent_telemetry_log_lines():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
