"""Seed telemetry with durable queue and background flushing.

This module records minimal, privacy‑preserving telemetry about seed
processing. Events are queued on disk via SQLite so they survive restarts
and are flushed in the background without blocking workers.

Only the following fields are transmitted and **never** the raw seed,
addresses or WIFs:

``app_instance_id`` – Persisted UUID identifying this installation.
``client_version`` – Software version from :mod:`config.settings`.
``mode`` – Seed processing mode (mnemonic, only_btc, puzzle, vanity,
           altcoin_derive).
``range_id`` – Optional range bucket identifier.
``seed_fingerprint`` – SHA256(seed_bytes || app_instance_id).
``timestamp_iso`` – Event timestamp in ISO‑8601 format.
``used`` / ``match_found`` – Result flags.
``machine_id`` – Stable per-machine identifier (opaque).
``machine_name`` – Human-friendly display name (mutable).
``range_recent`` – Bounded list of recently checked ranges.
``range_distribution`` – Normalized range metadata for density visualization.
``reference_overlays`` – Reserved stub for future range correlation.

The telemetry queue is capped at 100k entries and behaves as a ring buffer.
When offline, events remain on disk and are retried with exponential backoff
(capped at 5 minutes).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import multiprocessing

from config.directories import LOG_DIR
from config.telemetry import (
    AUTO_START_TELEMETRY_SERVICE,
    CLIENT_VERSION,
    SEED_TELEMETRY_ENABLED,
    TELEMETRY_BATCH_SIZE,
    TELEMETRY_CHECK_ENDPOINT,
    TELEMETRY_CHECK_TIMEOUT,
    TELEMETRY_ENDPOINT,
    TELEMETRY_FLUSH_SECONDS,
    TELEMETRY_MAX_BACKOFF,
    TELEMETRY_SERVICE_HOST,
    TELEMETRY_SERVICE_PORT,
)
from core.logger import get_logger
from utils.thread_guard import can_spawn_thread
from utils.machine_identity import get_machine_id, get_machine_name

logger = get_logger(__name__)

QUEUE_DB = Path(LOG_DIR) / "telemetry_queue.db"
INSTANCE_ID_PATH = Path(LOG_DIR) / "app_instance_id"
RANGE_RECENT_LIMIT = 50


def _get_app_id(path: Path = INSTANCE_ID_PATH) -> str:
    """Return a stable UUID for this installation."""

    if path.exists():
        return path.read_text().strip()
    import uuid

    app_id = str(uuid.uuid4())
    path.write_text(app_id)
    return app_id


class TelemetryClient:
    """Durable telemetry queue with background flushing."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        endpoint: str = TELEMETRY_ENDPOINT,
        batch_size: int = TELEMETRY_BATCH_SIZE,
        flush_seconds: int = TELEMETRY_FLUSH_SECONDS,
        max_backoff: int = TELEMETRY_MAX_BACKOFF,
        db_path: Path = QUEUE_DB,
        instance_id_path: Path = INSTANCE_ID_PATH,
    ) -> None:
        self.enabled = enabled
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.max_backoff = max_backoff
        self.db_path = Path(db_path)
        self.app_id = _get_app_id(instance_id_path)
        self.machine_id = get_machine_id()
        self.machine_name = get_machine_name(self.machine_id)
        self._recent_ranges: deque[Dict[str, Any]] = deque(maxlen=RANGE_RECENT_LIMIT)
        self._range_lock = threading.Lock()
        self._backoff = flush_seconds
        if self.enabled:
            self._init_db()
            try:
                logger.info(
                    f"[Telemetry] Client initialized | endpoint={self.endpoint} | "
                    f"flush={self.flush_seconds}s | batch={self.batch_size} | db={self.db_path}"
                )
            except Exception:
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            )
            """
        )
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.close()

    # ------------------------------ Queue ops ------------------------------
    def record_range_event(
        self,
        *,
        mode: str,
        range_id: str,
        start: int,
        end: int,
        space_min: Optional[int] = None,
        space_max: Optional[int] = None,
    ) -> None:
        """Record a bounded recent range observation for telemetry payloads."""

        if not self.enabled:
            return

        start_val, end_val = int(start), int(end)
        if end_val < start_val:
            start_val, end_val = end_val, start_val
        position = (start_val + end_val) // 2
        space_span = None
        if space_min is not None and space_max is not None and space_max > space_min:
            space_span = space_max - space_min
        normalized_position = (
            (position - space_min) / space_span
            if space_span and space_min is not None
            else None
        )
        normalized_span = (
            (end_val - start_val) / space_span if space_span else None
        )
        payload = {
            "mode": mode,
            "range_id": range_id,
            "start": start_val,
            "end": end_val,
            "position": position,
            "timestamp_iso": datetime.utcnow().isoformat() + "Z",
            "space_min": space_min,
            "space_max": space_max,
            "normalized_position": normalized_position,
            "normalized_span": normalized_span,
            "reference_overlays": [],
        }
        with self._range_lock:
            self._recent_ranges.append(payload)

    def _range_distribution(self, ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        summaries: Dict[str, Dict[str, Any]] = {}
        for entry in ranges:
            range_key = entry.get("range_id") or "default"
            summary = summaries.setdefault(
                range_key,
                {
                    "range_id": range_key,
                    "observed_count": 0,
                    "observed_min": None,
                    "observed_max": None,
                    "normalized_min": None,
                    "normalized_max": None,
                    "space_min": entry.get("space_min"),
                    "space_max": entry.get("space_max"),
                },
            )
            position = entry.get("position")
            normalized = entry.get("normalized_position")
            if isinstance(position, int):
                summary["observed_count"] += 1
                summary["observed_min"] = (
                    position
                    if summary["observed_min"] is None
                    else min(summary["observed_min"], position)
                )
                summary["observed_max"] = (
                    position
                    if summary["observed_max"] is None
                    else max(summary["observed_max"], position)
                )
            if isinstance(normalized, (float, int)):
                summary["normalized_min"] = (
                    normalized
                    if summary["normalized_min"] is None
                    else min(summary["normalized_min"], normalized)
                )
                summary["normalized_max"] = (
                    normalized
                    if summary["normalized_max"] is None
                    else max(summary["normalized_max"], normalized)
                )
        return list(summaries.values())

    def record_event(
        self,
        seed_bytes: bytes,
        *,
        mode: str,
        range_id: Optional[str],
        used: bool,
        match_found: bool,
    ) -> None:
        """Persist a telemetry event to the queue."""

        if not self.enabled:
            return

        fingerprint = hashlib.sha256(seed_bytes + self.app_id.encode()).hexdigest()
        with self._range_lock:
            recent_ranges = list(self._recent_ranges)
        payload = {
            "app_instance_id": self.app_id,
            "client_version": CLIENT_VERSION,
            "mode": mode,
            "range_id": range_id,
            "seed_fingerprint": fingerprint,
            "timestamp_iso": datetime.utcnow().isoformat() + "Z",
            "used": used,
            "match_found": match_found,
            "machine_id": self.machine_id,
            "machine_name": self.machine_name,
            "range_recent": recent_ranges,
            "range_distribution": self._range_distribution(recent_ranges),
            "reference_overlays": [],
        }
        data = json.dumps(payload)

        conn = self._connect()
        try:
            with conn:
                conn.execute("INSERT INTO telemetry(payload) VALUES (?)", (data,))
                conn.execute(
                    "DELETE FROM telemetry WHERE id NOT IN (SELECT id FROM telemetry ORDER BY id DESC LIMIT 100000)"
                )
        finally:
            conn.close()
        try:
            logger.debug(
                f"[Telemetry] Queued event | mode={mode} | range={range_id} | used={used} | match={match_found}"
            )
        except Exception:
            pass

    # ------------------------------- Flushing ------------------------------
    def _fetch_batch(self, conn: sqlite3.Connection) -> Iterable[tuple[int, str]]:
        cur = conn.execute(
            "SELECT id, payload FROM telemetry ORDER BY id ASC LIMIT ?",
            (self.batch_size,),
        )
        return cur.fetchall()

    def _send_batch(self, batch: Iterable[Dict[str, Any]]) -> requests.Response:
        response = requests.post(self.endpoint, json=list(batch), timeout=10)
        # Treat HTTP errors as failures so queued events are retried instead of
        # being dropped silently. ``raise_for_status`` preserves the response on
        # the exception for logging/backoff handling.
        response.raise_for_status()
        return response

    def flush_once(self) -> bool:
        """Flush a single batch from the queue.

        Returns ``True`` on success, ``False`` on failure. When disabled this
        method is a no-op returning ``True``.
        """

        if not self.enabled:
            return True

        conn = self._connect()
        try:
            batch = self._fetch_batch(conn)
            if not batch:
                self._backoff = self.flush_seconds
                return True
            ids, payloads = zip(*batch)
            try:
                logger.info(f"[Telemetry] Flushing {len(ids)} event(s) to {self.endpoint}")
                response = self._send_batch([json.loads(p) for p in payloads])
            except Exception as exc:
                self._backoff = min(self._backoff * 2, self.max_backoff)
                try:
                    logger.warning(
                        "[Telemetry] Flush failed; backing off to %ss | reason=%s",
                        self._backoff,
                        getattr(getattr(exc, "response", None), "status_code", exc),
                    )
                except Exception:
                    pass
                return False
            with conn:
                conn.execute(
                    f"DELETE FROM telemetry WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
            self._backoff = self.flush_seconds
            try:
                status = getattr(response, "status_code", "?")
                logger.info(
                    f"[Telemetry] Flush succeeded | sent={len(ids)} | status={status}"
                )
            except Exception:
                pass
            return True
        finally:
            conn.close()

    def start(self, shutdown_event: threading.Event) -> None:
        """Start the background flusher thread."""

        if not self.enabled:
            return

        def _loop() -> None:
            while not shutdown_event.is_set():
                ok = self.flush_once()
                wait = self.flush_seconds if ok else self._backoff
                shutdown_event.wait(wait)
            self.flush_once()

        if can_spawn_thread("telemetry"):
            threading.Thread(target=_loop, name="telemetry", daemon=True).start()
        else:
            logger.warning("[Telemetry] Skipping telemetry thread; at thread limit")


_CLIENT: Optional[TelemetryClient] = None


def start_telemetry(shutdown_event: threading.Event) -> None:
    """Initialize and start the global telemetry client."""

    if not SEED_TELEMETRY_ENABLED:
        return

    global _CLIENT
    _CLIENT = TelemetryClient()
    _CLIENT.start(shutdown_event)
    try:
        logger.info("[Telemetry] Background flusher thread started")
    except Exception:
        pass


def _run_uvicorn(host: str, port: int) -> None:  # pragma: no cover - runtime integration
    try:
        from telemetry_service.__main__ import main as svc_main
        # svc_main calls uvicorn.run(app,...)
        import os
        os.environ.setdefault("UVICORN_WORKERS", "1")
        os.environ["TELEMETRY_SERVICE_HOST"] = str(host)
        os.environ["TELEMETRY_SERVICE_PORT"] = str(port)
        svc_main()
    except Exception:
        # If service is missing dependencies, do not crash the app
        pass


def start_embedded_telemetry_service() -> Optional[multiprocessing.Process]:
    """Start the embedded FastAPI telemetry service in a child process if enabled.

    Returns the process object, or None if not started.
    """
    if not SEED_TELEMETRY_ENABLED:
        return None
    if not AUTO_START_TELEMETRY_SERVICE:
        return None
    try:
        host = TELEMETRY_SERVICE_HOST or "0.0.0.0"
        port = int(TELEMETRY_SERVICE_PORT)
    except Exception:
        host, port = "0.0.0.0", 8000
    try:
        p = multiprocessing.Process(target=_run_uvicorn, args=(host, port), daemon=True)
        p.start()
        return p
    except Exception:
        return None


# ---------------------------- Central status check ----------------------------
def check_seed_seen(seed_bytes: bytes, *, mode: str, range_id: Optional[str] = None) -> bool:
    """Return True if the central telemetry database marks this seed as seen.

    Network failures are treated as "unknown/not seen" and return False. The
    server is expected to accept either POST or GET to
    :data:`config.telemetry.TELEMETRY_CHECK_ENDPOINT` with a JSON body or query string:

    { "seed_fingerprint": SHA256(seed||app_id), "mode": mode, "range_id": str|None }

    The response should be JSON like {"used": true|false}.
    """

    try:
        app_id = _CLIENT.app_id if _CLIENT else _get_app_id()
        fp = hashlib.sha256(seed_bytes + app_id.encode()).hexdigest()
        url = TELEMETRY_CHECK_ENDPOINT or TELEMETRY_ENDPOINT
        timeout = TELEMETRY_CHECK_TIMEOUT or 1.5
        payload = {"seed_fingerprint": fp, "mode": mode, "range_id": range_id}
        try:
            r = requests.post(url, json=payload, timeout=timeout)
        except Exception:
            # Fallback to GET with query params if POST fails in some setups
            try:
                r = requests.get(url, params=payload, timeout=timeout)
            except Exception:
                return False
        if r is None or getattr(r, "status_code", 599) >= 400:
            return False
        try:
            data = r.json()
        except Exception:
            return False
        return bool(data.get("used", False))
    except Exception:
        # Never block callers; on any error default to not seen so local work continues
        return False


def record_seed_event(
    seed_bytes: bytes,
    *,
    mode: str,
    range_id: Optional[str],
    used: bool,
    match_found: bool,
) -> None:
    """Record a telemetry event if the global client is active."""

    if _CLIENT is None:
        return
    _CLIENT.record_event(
        seed_bytes,
        mode=mode,
        range_id=range_id,
        used=used,
        match_found=match_found,
    )


def record_range_event(
    *,
    mode: str,
    range_id: str,
    start: int,
    end: int,
    space_min: Optional[int] = None,
    space_max: Optional[int] = None,
) -> None:
    """Record a range observation for telemetry payload enrichment."""

    if _CLIENT is None:
        return
    _CLIENT.record_range_event(
        mode=mode,
        range_id=range_id,
        start=start,
        end=end,
        space_min=space_min,
        space_max=space_max,
    )
