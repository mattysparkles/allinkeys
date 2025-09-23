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

The telemetry queue is capped at 100k entries and behaves as a ring buffer.
When offline, events remain on disk and are retried with exponential backoff
(capped at 5 minutes).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests

from config import settings

QUEUE_DB = Path(settings.LOG_DIR) / "telemetry_queue.db"
INSTANCE_ID_PATH = Path(settings.LOG_DIR) / "app_instance_id"


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
        endpoint: str = settings.TELEMETRY_ENDPOINT,
        batch_size: int = settings.TELEMETRY_BATCH_SIZE,
        flush_seconds: int = settings.TELEMETRY_FLUSH_SECONDS,
        max_backoff: int = settings.TELEMETRY_MAX_BACKOFF,
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
        self._backoff = flush_seconds
        if self.enabled:
            self._init_db()

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
        payload = {
            "app_instance_id": self.app_id,
            "client_version": settings.CLIENT_VERSION,
            "mode": mode,
            "range_id": range_id,
            "seed_fingerprint": fingerprint,
            "timestamp_iso": datetime.utcnow().isoformat() + "Z",
            "used": used,
            "match_found": match_found,
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

    # ------------------------------- Flushing ------------------------------
    def _fetch_batch(self, conn: sqlite3.Connection) -> Iterable[tuple[int, str]]:
        cur = conn.execute(
            "SELECT id, payload FROM telemetry ORDER BY id ASC LIMIT ?",
            (self.batch_size,),
        )
        return cur.fetchall()

    def _send_batch(self, batch: Iterable[Dict[str, Any]]) -> None:
        requests.post(self.endpoint, json=list(batch), timeout=10)

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
                self._send_batch([json.loads(p) for p in payloads])
            except Exception:
                self._backoff = min(self._backoff * 2, self.max_backoff)
                return False
            with conn:
                conn.execute(
                    f"DELETE FROM telemetry WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
            self._backoff = self.flush_seconds
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

        threading.Thread(target=_loop, name="telemetry", daemon=True).start()


_CLIENT: Optional[TelemetryClient] = None


def start_telemetry(shutdown_event: threading.Event) -> None:
    """Initialize and start the global telemetry client."""

    if not settings.SEED_TELEMETRY_ENABLED:
        return

    global _CLIENT
    _CLIENT = TelemetryClient()
    _CLIENT.start(shutdown_event)


# ---------------------------- Central status check ----------------------------
def check_seed_seen(seed_bytes: bytes, *, mode: str, range_id: Optional[str] = None) -> bool:
    """Return True if the central telemetry database marks this seed as seen.

    Network failures are treated as "unknown/not seen" and return False. The
    server is expected to accept either POST or GET to the
    ``settings.TELEMETRY_CHECK_ENDPOINT`` with a JSON body or query string:

    { "seed_fingerprint": SHA256(seed||app_id), "mode": mode, "range_id": str|None }

    The response should be JSON like {"used": true|false}.
    """

    try:
        app_id = _CLIENT.app_id if _CLIENT else _get_app_id()
        fp = hashlib.sha256(seed_bytes + app_id.encode()).hexdigest()
        url = getattr(settings, "TELEMETRY_CHECK_ENDPOINT", settings.TELEMETRY_ENDPOINT)
        timeout = getattr(settings, "TELEMETRY_CHECK_TIMEOUT", 1.5) or 1.5
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
