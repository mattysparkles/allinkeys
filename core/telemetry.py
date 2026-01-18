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
import os
import sqlite3
import threading
import time
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
    CONTROL_ENDPOINT,
    CONTROL_POLL_SECONDS,
    TELEMETRY_BATCH_SIZE,
    TELEMETRY_CHECK_ENDPOINT,
    TELEMETRY_CHECK_CACHE_TTL_SECONDS,
    TELEMETRY_CHECK_TIMEOUT,
    TELEMETRY_ENDPOINT,
    TELEMETRY_FLUSH_SECONDS,
    TELEMETRY_MAX_BACKOFF,
    TELEMETRY_SERVICE_HOST,
    TELEMETRY_SERVICE_PORT,
)
from core.logger import get_logger, log_with_context
from core.worker_bootstrap import _safe_inc_metric
from utils.thread_guard import can_spawn_thread
from utils.machine_identity import get_machine_id, get_machine_name

logger = get_logger(__name__)

QUEUE_DB = Path(LOG_DIR) / "telemetry_queue.db"
INSTANCE_ID_PATH = Path(LOG_DIR) / "app_instance_id"
MACHINE_ID_PATH = Path(LOG_DIR) / ".machine_id"
CONTROL_STATE_PATH = Path(LOG_DIR) / "control_state.json"
RANGE_RECENT_LIMIT = 50
CHECK_CACHE_MAX_SIZE = 10_000
AUTH_TOKEN_ENV = "AUTH_TOKEN"

_CHECK_CACHE: Dict[tuple[str, str, Optional[str]], tuple[bool, float]] = {}
_CHECK_CACHE_LOCK = threading.Lock()


def _coerce_percent(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().rstrip("%").strip()
        if not cleaned or cleaned.lower() in {"n/a", "na"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _system_metrics_payload() -> Dict[str, Any]:
    try:
        from core.dashboard import get_current_metrics

        metrics = get_current_metrics()
    except Exception:
        metrics = {}
    cpu_percent = _coerce_percent(metrics.get("cpu_percent"))
    ram_percent = _coerce_percent(metrics.get("ram_percent"))
    disk_free_percent = _coerce_percent(metrics.get("disk_free_percent"))
    gpu_load_percent = _coerce_percent(metrics.get("gpu_load_percent"))
    gpu_name = metrics.get("gpu_name")
    if not isinstance(gpu_name, str) or not gpu_name.strip() or gpu_name == "N/A":
        gpu_name = None
    time_to_disk_full = metrics.get("time_to_disk_full")
    if not isinstance(time_to_disk_full, str) or time_to_disk_full == "N/A":
        time_to_disk_full = None
    return {
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "disk_free_percent": disk_free_percent,
        "gpu_load_percent": gpu_load_percent,
        "gpu_name": gpu_name,
        "time_to_disk_full": time_to_disk_full,
    }


def _get_app_id(path: Path = INSTANCE_ID_PATH) -> str:
    """Return a stable UUID for this installation."""

    if path.exists():
        return path.read_text().strip()
    import uuid

    app_id = str(uuid.uuid4())
    path.write_text(app_id)
    return app_id


def _load_machine_id(path: Path = MACHINE_ID_PATH) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _save_machine_id(machine_id: str, path: Path = MACHINE_ID_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(machine_id, encoding="utf-8")
    except Exception:
        return


def _telemetry_log_context(*, mode: Optional[str] = None, range_id: Optional[str] = None, endpoint: Optional[str] = None) -> dict:
    return {
        "mode": mode,
        "range_id": range_id,
        "endpoint": endpoint,
    }


class TelemetryClient:
    """Durable telemetry queue with background flushing."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        endpoint: str = TELEMETRY_ENDPOINT,
        control_endpoint: Optional[str] = CONTROL_ENDPOINT,
        batch_size: int = TELEMETRY_BATCH_SIZE,
        flush_seconds: int = TELEMETRY_FLUSH_SECONDS,
        max_backoff: int = TELEMETRY_MAX_BACKOFF,
        db_path: Path = QUEUE_DB,
        instance_id_path: Path = INSTANCE_ID_PATH,
        machine_id_path: Path = MACHINE_ID_PATH,
        auth_token: Optional[str] = None,
    ) -> None:
        self.enabled = enabled
        self.endpoint = endpoint
        self.control_endpoint = control_endpoint or None
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.max_backoff = max_backoff
        self.db_path = Path(db_path)
        self.app_id = _get_app_id(instance_id_path)
        self.machine_id_path = Path(machine_id_path)
        self.machine_id = _load_machine_id(self.machine_id_path)
        self.hardware_machine_id = get_machine_id()
        self.machine_name = get_machine_name(self.hardware_machine_id)
        self.auth_token = auth_token or os.getenv(AUTH_TOKEN_ENV)
        self._recent_ranges: deque[Dict[str, Any]] = deque(maxlen=RANGE_RECENT_LIMIT)
        self._range_lock = threading.Lock()
        self._backoff = flush_seconds
        self._flusher_thread: Optional[threading.Thread] = None
        self._control_thread: Optional[threading.Thread] = None
        if self.enabled:
            self._init_db()
            try:
                log_with_context(
                    logger,
                    "INFO",
                    f"[Telemetry] Client initialized | endpoint={self.endpoint} | "
                    f"flush={self.flush_seconds}s | batch={self.batch_size} | db={self.db_path}",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._sanity_check(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
                """
            )
            return conn
        except sqlite3.OperationalError as exc:
            self._reset_db(exc)
            try:
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._sanity_check(conn)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payload TEXT NOT NULL
                    )
                    """
                )
                return conn
            except sqlite3.OperationalError:
                raise

    def _authorization_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            return {}
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _machine_endpoints(self, machine_id: Optional[str] = None) -> tuple[str, str]:
        endpoint = self.endpoint.rstrip("/")
        if "/v1/" in endpoint:
            base = endpoint.split("/v1/")[0] + "/v1"
        else:
            base = endpoint
        register_url = f"{base}/machines/register"
        telemetry_url = (
            f"{base}/machines/{machine_id}/telemetry" if machine_id else f"{base}/machines/telemetry"
        )
        return register_url, telemetry_url

    def _control_base(self) -> Optional[str]:
        endpoint = (self.control_endpoint or self.endpoint).strip().rstrip("/")
        if not endpoint:
            return None
        if "/v1/" in endpoint:
            return endpoint.split("/v1/")[0] + "/v1"
        if endpoint.endswith("/v1"):
            return endpoint
        return endpoint

    def _control_urls(self, machine_id: str) -> Optional[tuple[str, str]]:
        base = self._control_base()
        if not base:
            return None
        poll_url = f"{base}/machines/{machine_id}/control/poll"
        ack_url = f"{base}/machines/{machine_id}/control/ack"
        return poll_url, ack_url

    def _update_control_state(self, updates: Dict[str, Any]) -> None:
        state: Dict[str, Any] = {}
        try:
            if CONTROL_STATE_PATH.exists():
                state = json.loads(CONTROL_STATE_PATH.read_text())
        except Exception:
            state = {}
        state.update(updates)
        state["updated_at"] = datetime.utcnow().isoformat() + "Z"
        try:
            CONTROL_STATE_PATH.write_text(json.dumps(state, indent=2))
        except Exception:
            return

    def _apply_control_command(self, command: Dict[str, Any]) -> bool:
        cmd = str(command.get("command") or "").lower()
        value = command.get("value")
        if cmd in {"pause", "resume"}:
            try:
                from core.dashboard import get_pause_event, module_pause_events

                events = [ev for ev in module_pause_events.values() if ev]
                if not events:
                    default_event = get_pause_event()
                    if default_event:
                        events = [default_event]
                for event in events:
                    if cmd == "pause":
                        event.set()
                    else:
                        event.clear()
            except Exception:
                return False
            self._update_control_state(
                {
                    "paused": cmd == "pause",
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            return True
        if cmd == "set_mode":
            self._update_control_state(
                {
                    "mode": value,
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            return True
        if cmd == "set_range":
            self._update_control_state(
                {
                    "range": value,
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            return True
        return False

    def _poll_control_commands(self) -> List[Dict[str, Any]]:
        if not self._ensure_machine_registered():
            return []
        if not self.auth_token:
            return []
        urls = self._control_urls(self.machine_id or "")
        if not urls:
            return []
        poll_url, _ = urls
        try:
            response = requests.get(
                poll_url,
                headers=self._authorization_headers(),
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Control poll failed | reason=%s",
                    getattr(getattr(exc, "response", None), "status_code", exc),
                    **_telemetry_log_context(endpoint=poll_url),
                )
            except Exception:
                pass
            return []
        commands = payload.get("commands") if isinstance(payload, dict) else None
        if isinstance(commands, list):
            return [cmd for cmd in commands if isinstance(cmd, dict)]
        return []

    def _ack_control_command(self, command_id: int) -> None:
        if not self.machine_id:
            return
        urls = self._control_urls(self.machine_id)
        if not urls:
            return
        _, ack_url = urls
        try:
            requests.post(
                ack_url,
                json={"command_id": command_id},
                headers=self._authorization_headers(),
                timeout=10,
            )
        except Exception:
            return

    def _ensure_machine_registered(self) -> bool:
        if self.machine_id:
            return True
        if not self.auth_token:
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Missing AUTH_TOKEN; cannot register machine",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return False
        return self._register_machine()

    def _register_machine(self) -> bool:
        register_url, _ = self._machine_endpoints()
        metrics = _system_metrics_payload()
        payload = {
            "machine_name": self.machine_name,
            "gpu_info": metrics.get("gpu_name"),
            "version": CLIENT_VERSION,
        }
        try:
            response = requests.post(
                register_url,
                json=payload,
                headers=self._authorization_headers(),
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Machine registration failed | reason=%s",
                    getattr(getattr(exc, "response", None), "status_code", exc),
                    **_telemetry_log_context(endpoint=register_url),
                )
            except Exception:
                pass
            return False
        machine_id = data.get("machine_id") if isinstance(data, dict) else None
        if not machine_id:
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Machine registration response missing machine_id",
                    **_telemetry_log_context(endpoint=register_url),
                )
            except Exception:
                pass
            return False
        self.machine_id = str(machine_id)
        _save_machine_id(self.machine_id, self.machine_id_path)
        return True

    def _sanity_check(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("PRAGMA quick_check")
        except sqlite3.OperationalError as exc:
            raise exc

    def _reset_db(self, exc: Exception) -> None:
        try:
            logger.warning("[Telemetry] SQLite error; resetting telemetry DB: %s", exc)
        except Exception:
            pass
        try:
            if self.db_path.exists():
                self.db_path.unlink()
        except Exception as delete_exc:
            try:
                logger.warning("[Telemetry] Failed to delete telemetry DB: %s", delete_exc)
            except Exception:
                pass
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.close()
        except Exception as reset_exc:
            try:
                logger.warning("[Telemetry] Telemetry DB reset failed: %s", reset_exc)
            except Exception:
                pass

    def _init_db(self) -> None:
        try:
            conn = self._connect()
        except sqlite3.OperationalError:
            return
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
            **_system_metrics_payload(),
        }
        data = json.dumps(payload)

        try:
            conn = self._connect()
        except sqlite3.OperationalError:
            return
        try:
            with conn:
                conn.execute("INSERT INTO telemetry(payload) VALUES (?)", (data,))
                conn.execute(
                    "DELETE FROM telemetry WHERE id NOT IN (SELECT id FROM telemetry ORDER BY id DESC LIMIT 100000)"
                )
        except sqlite3.OperationalError as exc:
            self._reset_db(exc)
        finally:
            conn.close()
        try:
            log_with_context(
                logger,
                "DEBUG",
                f"[Telemetry] Queued event | mode={mode} | range={range_id} | used={used} | match={match_found}",
                **_telemetry_log_context(mode=mode, range_id=range_id),
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
        if not self._ensure_machine_registered():
            raise RuntimeError("Machine registration required")
        if not self.auth_token:
            raise RuntimeError("Missing AUTH_TOKEN for telemetry upload")
        _, telemetry_url = self._machine_endpoints(self.machine_id)
        payload = []
        for item in batch:
            enriched = dict(item)
            enriched.setdefault("machine_id", self.machine_id)
            enriched.setdefault("machine_name", self.machine_name)
            payload.append(enriched)
        response = requests.post(
            telemetry_url,
            json=payload,
            headers=self._authorization_headers(),
            timeout=10,
        )
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

        try:
            conn = self._connect()
        except sqlite3.OperationalError:
            self._backoff = min(self._backoff * 2, self.max_backoff)
            return False
        try:
            batch = self._fetch_batch(conn)
            if not batch:
                self._backoff = self.flush_seconds
                return True
            ids, payloads = zip(*batch)
            try:
                log_with_context(
                    logger,
                    "INFO",
                    f"[Telemetry] Flushing {len(ids)} event(s) to {self.endpoint}",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
                response = self._send_batch([json.loads(p) for p in payloads])
            except Exception as exc:
                self._backoff = min(self._backoff * 2, self.max_backoff)
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Flush failed; backing off to %ss | reason=%s",
                        self._backoff,
                        getattr(getattr(exc, "response", None), "status_code", exc),
                        **_telemetry_log_context(endpoint=self.endpoint),
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
                log_with_context(
                    logger,
                    "INFO",
                    f"[Telemetry] Flush succeeded | sent={len(ids)} | status={status}",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return True
        except sqlite3.OperationalError as exc:
            self._reset_db(exc)
            self._backoff = min(self._backoff * 2, self.max_backoff)
            return False
        finally:
            conn.close()

    def start(self, shutdown_event: threading.Event) -> None:
        """Start the background flusher thread."""

        if not self.enabled:
            return
        if getattr(self, "_flusher_thread", None) is not None and self._flusher_thread.is_alive():
            return

        def _loop() -> None:
            while not shutdown_event.is_set():
                ok = self.flush_once()
                wait = self.flush_seconds if ok else self._backoff
                shutdown_event.wait(wait)
            self.flush_once()

        if can_spawn_thread("telemetry"):
            self._flusher_thread = threading.Thread(target=_loop, name="telemetry", daemon=True)
            self._flusher_thread.start()
        else:
            logger.warning("[Telemetry] Skipping telemetry thread; at thread limit")

    def start_control_polling(self, shutdown_event: threading.Event) -> None:
        """Start polling for control commands if configured."""

        if not self.enabled:
            return
        if self._control_thread is not None and self._control_thread.is_alive():
            return
        if not (self.control_endpoint or self.endpoint):
            return

        def _loop() -> None:
            while not shutdown_event.is_set():
                commands = self._poll_control_commands()
                for command in commands:
                    command_id = command.get("id")
                    if not isinstance(command_id, int):
                        continue
                    applied = self._apply_control_command(command)
                    if applied:
                        self._ack_control_command(command_id)
                shutdown_event.wait(CONTROL_POLL_SECONDS)

        if can_spawn_thread("telemetry_control"):
            self._control_thread = threading.Thread(
                target=_loop,
                name="telemetry_control",
                daemon=True,
            )
            self._control_thread.start()
        else:
            logger.warning("[Telemetry] Skipping control polling thread; at thread limit")


_CLIENT: Optional[TelemetryClient] = None


def start_telemetry(shutdown_event: threading.Event) -> None:
    """Initialize and start the global telemetry client."""

    if not SEED_TELEMETRY_ENABLED:
        return

    global _CLIENT
    if _CLIENT is None:
        _CLIENT = TelemetryClient()
    _CLIENT.start(shutdown_event)
    _CLIENT.start_control_polling(shutdown_event)
    try:
        log_with_context(
            logger,
            "INFO",
            "[Telemetry] Background flusher thread started",
            **_telemetry_log_context(endpoint=_CLIENT.endpoint if _CLIENT else None),
        )
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


def _evict_expired_check_cache(now: float) -> None:
    expired_keys = [
        key for key, (_, expires_at) in _CHECK_CACHE.items() if expires_at <= now
    ]
    for key in expired_keys:
        _CHECK_CACHE.pop(key, None)


def _trim_check_cache(max_size: int) -> None:
    if len(_CHECK_CACHE) <= max_size:
        return
    overflow = len(_CHECK_CACHE) - max_size
    for key, _ in sorted(_CHECK_CACHE.items(), key=lambda item: item[1][1])[:overflow]:
        _CHECK_CACHE.pop(key, None)


def _get_cached_seed_check(
    cache_key: tuple[str, str, Optional[str]],
) -> Optional[bool]:
    now = time.monotonic()
    with _CHECK_CACHE_LOCK:
        _evict_expired_check_cache(now)
        entry = _CHECK_CACHE.get(cache_key)
        if entry is None:
            return None
        used, expires_at = entry
        if expires_at <= now:
            _CHECK_CACHE.pop(cache_key, None)
            return None
        return used


def _set_cached_seed_check(
    cache_key: tuple[str, str, Optional[str]],
    used: bool,
) -> None:
    if not used:
        # Avoid caching negatives to prevent suppressing real matches.
        return
    ttl_seconds = TELEMETRY_CHECK_CACHE_TTL_SECONDS
    if ttl_seconds <= 0:
        return
    now = time.monotonic()
    expires_at = now + ttl_seconds
    with _CHECK_CACHE_LOCK:
        _evict_expired_check_cache(now)
        _CHECK_CACHE[cache_key] = (used, expires_at)
        _trim_check_cache(CHECK_CACHE_MAX_SIZE)


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
        cache_key = (fp, mode, range_id)
        cached = _get_cached_seed_check(cache_key)
        if cached is not None:
            _safe_inc_metric("telemetry_cache_hits")
            return cached
        _safe_inc_metric("telemetry_cache_misses")
        url = TELEMETRY_CHECK_ENDPOINT or TELEMETRY_ENDPOINT
        timeout = TELEMETRY_CHECK_TIMEOUT or 1.5
        payload = {"seed_fingerprint": fp, "mode": mode, "range_id": range_id}
        headers = {}
        auth_token = _CLIENT.auth_token if _CLIENT else os.getenv(AUTH_TOKEN_ENV)
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception:
            # Fallback to GET with query params if POST fails in some setups
            try:
                r = requests.get(url, params=payload, headers=headers, timeout=timeout)
            except Exception:
                return False
        if r is None or getattr(r, "status_code", 599) >= 400:
            return False
        try:
            data = r.json()
        except Exception:
            return False
        used = bool(data.get("used", False))
        _set_cached_seed_check(cache_key, used)
        return used
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
