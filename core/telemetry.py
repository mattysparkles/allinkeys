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
``metrics`` – Full dashboard metric snapshot.
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
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import multiprocessing

import config.settings as settings

from config.directories import BASE_DIR, LOG_DIR
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
    TELEMETRY_API_KEY,
    TELEMETRY_SERVICE_HOST,
    TELEMETRY_SERVICE_PORT,
    TELEMETRY_SNAPSHOT_SECONDS,
)
from core.logger import get_logger, log_with_context
from core.seed_queue import enqueue_many, parse_queue_value, size as seed_queue_size
from core.worker_bootstrap import _safe_inc_metric, _safe_set_metric
from utils.thread_guard import can_spawn_thread
from utils.machine_identity import (
    get_machine_id,
    get_machine_identity,
    get_machine_name,
    get_machine_name_state,
    set_machine_name,
    suggest_machine_name,
)
from telemetry_contract import (
    ControlCapabilities,
    MachineIdentity,
    MachineTelemetrySnapshot,
    ResourceStats,
    RuntimeStats,
)

logger = get_logger(__name__)

QUEUE_DB = Path(LOG_DIR) / "telemetry_queue.db"
INSTANCE_ID_PATH = Path(LOG_DIR) / "app_instance_id"
MACHINE_ID_PATH = Path(LOG_DIR) / ".machine_id"
CONTROL_STATE_PATH = Path(LOG_DIR) / "control_state.json"
RANGE_RECENT_LIMIT = 50
CHECK_CACHE_MAX_SIZE = 10_000
AUTH_TOKEN_ENV = "AUTH_TOKEN"
TOKEN_STORE_PATH = Path(BASE_DIR) / "config" / ".telemetry_token"
LOCAL_TELEMETRY_PATH = Path(BASE_DIR) / "config" / "local_telemetry.json"
PAIR_POLL_DEFAULT_SECONDS = 3

_MISSING_TOKEN_LOGGED = False
_INVALID_TOKEN_LOGGED = False

_CHECK_CACHE: Dict[tuple[str, str, Optional[str]], tuple[bool, float]] = {}
_CHECK_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class TelemetrySetupOutcome:
    token: Optional[str]
    disabled: bool = False


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return


def _load_local_config(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or LOCAL_TELEMETRY_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_local_config(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or LOCAL_TELEMETRY_PATH
    try:
        _ensure_parent(path)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def load_persisted_auth_token(path: Optional[Path] = None) -> Optional[str]:
    path = path or TOKEN_STORE_PATH
    try:
        value = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return value or None


def persist_auth_token(token: str, path: Optional[Path] = None) -> None:
    path = path or TOKEN_STORE_PATH
    cleaned = (token or "").strip()
    if not cleaned:
        return
    try:
        _ensure_parent(path)
        path.write_text(cleaned, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        return


def clear_persisted_auth_token(path: Optional[Path] = None) -> None:
    path = path or TOKEN_STORE_PATH
    try:
        if path.exists():
            path.unlink()
    except Exception:
        return


def telemetry_opted_out(path: Optional[Path] = None) -> bool:
    return bool(_load_local_config(path).get("telemetry_disabled", False))


def set_telemetry_opt_out(disabled: bool, path: Optional[Path] = None) -> None:
    data = _load_local_config(path)
    data["telemetry_disabled"] = bool(disabled)
    _save_local_config(data, path)


def _resolve_auth_token(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    env_value = os.getenv(AUTH_TOKEN_ENV)
    if env_value:
        return env_value
    return load_persisted_auth_token()


def _is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _telemetry_base_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if "/v1/" in endpoint:
        return endpoint.split("/v1/")[0] + "/v1"
    if endpoint.endswith("/v1"):
        return endpoint
    return endpoint


def _telemetry_root_url(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if "/v1/" in endpoint:
        return endpoint.split("/v1/")[0]
    if endpoint.endswith("/v1"):
        return endpoint[:-3]
    return endpoint


def _telemetry_headers(token: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    api_key = TELEMETRY_API_KEY.strip() if TELEMETRY_API_KEY else ""
    if api_key:
        headers["X-API-Key"] = api_key
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_uptime_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None
    return float(hours * 3600 + minutes * 60 + seconds)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%"))
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return str(value)
    return str(value)


def _infer_process_state(metrics: Dict[str, Any]) -> Optional[str]:
    state = metrics.get("global_run_state")
    if isinstance(state, str) and state.strip():
        return state
    status = metrics.get("status") or {}
    if isinstance(status, dict):
        running = any(str(val).lower() == "running" for val in status.values())
        paused = any(str(val).lower() == "paused" for val in status.values())
        if paused:
            return "paused"
        if running:
            return "running"
    return None


def build_range_distribution(ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summaries: Dict[str, Dict[str, Any]] = {}
    for entry in ranges:
        range_id = entry.get("range_id")
        if not isinstance(range_id, str) or not range_id.strip():
            continue
        range_value = entry.get("range_value")
        summary = summaries.setdefault(
            range_id,
            {
                "range_id": range_id,
                "range_value": range_value if isinstance(range_value, str) else None,
                "observed_count": 0,
                "observed_min": None,
                "observed_max": None,
                "normalized_min": None,
                "normalized_max": None,
                "space_min": entry.get("space_min"),
                "space_max": entry.get("space_max"),
            },
        )
        if summary.get("range_value") is None:
            start = entry.get("start")
            end = entry.get("end")
            if isinstance(start, int) and isinstance(end, int):
                start_val, end_val = (start, end) if start <= end else (end, start)
                summary["range_value"] = (
                    f"0x{start_val:064x}-0x{end_val:064x}"
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
        normalized_min = entry.get("normalized_min")
        normalized_max = entry.get("normalized_max")
        normalized_span = entry.get("normalized_span")
        if isinstance(normalized_min, (float, int)) and isinstance(
            normalized_max, (float, int)
        ):
            min_val = float(normalized_min)
            max_val = float(normalized_max)
        elif isinstance(normalized, (float, int)):
            normalized_val = float(normalized)
            if isinstance(normalized_span, (float, int)) and normalized_span > 0:
                half_span = float(normalized_span) / 2
                min_val = normalized_val - half_span
                max_val = normalized_val + half_span
            else:
                min_val = normalized_val
                max_val = normalized_val
        else:
            min_val = None
            max_val = None
        if min_val is not None and max_val is not None:
            min_val = max(0.0, min(1.0, min_val))
            max_val = max(0.0, min(1.0, max_val))
            summary["normalized_min"] = (
                min_val
                if summary["normalized_min"] is None
                else min(summary["normalized_min"], min_val)
            )
            summary["normalized_max"] = (
                max_val
                if summary["normalized_max"] is None
                else max(summary["normalized_max"], max_val)
            )
    return list(summaries.values())


def _snapshot_from_metrics(
    metrics: Dict[str, Any],
    *,
    machine_id: str,
    machine_name: Optional[str],
    machine_identity: Optional[str],
    display_name: Optional[str],
    app_instance_id: str,
    client_version: Optional[str],
    recent_ranges: Optional[List[Dict[str, Any]]] = None,
) -> MachineTelemetrySnapshot:
    last_activity = metrics.get("last_activity_ts")
    if not last_activity:
        last_rotation = metrics.get("last_rotation")
        if isinstance(last_rotation, (int, float)):
            last_activity = datetime.utcfromtimestamp(last_rotation).isoformat() + "Z"
    runtime = RuntimeStats(
        mode=metrics.get("active_mode"),
        keys_per_sec=_coerce_float(metrics.get("keys_per_sec")),
        total_keys=_coerce_float(metrics.get("keys_generated_lifetime")),
        uptime_seconds=_parse_uptime_seconds(metrics.get("uptime")),
        process_state=_infer_process_state(metrics),
        last_activity_ts=last_activity,
        last_error=str(metrics.get("last_popen_error") or "") or None,
    )
    resources = ResourceStats(
        cpu_percent=_coerce_float(metrics.get("cpu_percent")),
        ram_percent=_coerce_float(metrics.get("ram_percent")),
        disk_free_percent=_coerce_float(metrics.get("disk_free_percent")),
        gpu_load_percent=_coerce_float(metrics.get("gpu_load_percent")),
        gpu_name=str(metrics.get("gpu_name") or "") or None,
        time_to_disk_full=str(metrics.get("time_to_disk_full") or "") or None,
    )
    identity = MachineIdentity(
        machine_id=machine_id,
        machine_name=machine_name,
        machine_identity=machine_identity,
        display_name=display_name,
        app_instance_id=app_instance_id,
        client_version=client_version,
    )
    distribution = build_range_distribution(recent_ranges or [])
    metrics_payload = _json_safe(metrics) if metrics else None
    return MachineTelemetrySnapshot(
        identity=identity,
        runtime=runtime,
        resources=resources,
        capabilities=ControlCapabilities(),
        range_recent=recent_ranges or None,
        range_distribution=distribution or None,
        reference_overlays=[],
        metrics=metrics_payload,
    )


def _valid_token_format(token: str) -> bool:
    cleaned = token.strip()
    if len(cleaned) < 12:
        return False
    return bool(re.match(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$", cleaned))


def _attempt_machine_registration(
    *,
    endpoint: str,
    token: str,
    machine_name: str,
    requests_module=requests,
) -> tuple[bool, Optional[str], str]:
    register_url = f"{_telemetry_base_url(endpoint)}/machines/register"
    metrics = _system_metrics_payload()
    payload = {
        "machine_name": machine_name,
        "gpu_info": metrics.get("gpu_name"),
        "version": CLIENT_VERSION,
    }
    try:
        response = requests_module.post(
            register_url,
            json=payload,
            headers=_telemetry_headers(token),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        status_label = f"HTTP {status}" if status else str(exc)
        return False, None, status_label
    machine_id = data.get("machine_id") if isinstance(data, dict) else None
    if not machine_id:
        return False, None, "Missing machine_id in response"
    return True, str(machine_id), ""


def _validate_auth_token(
    *,
    endpoint: str,
    token: str,
    requests_module=requests,
) -> Optional[bool]:
    root = _telemetry_root_url(endpoint)
    if not root:
        return None
    try:
        response = requests_module.get(
            f"{root}/me",
            headers=_telemetry_headers(token),
            timeout=10,
        )
    except Exception:
        return None
    if response.status_code == 200:
        return True
    if response.status_code in {401, 403}:
        return False
    return None


def _print_disclosure(output_func, endpoint: str) -> None:
    output_func("[Telemetry] Telemetry is optional and helps improve AllInKeys.")
    output_func("[Telemetry] Endpoints: telemetry.sparkleserver.site")
    output_func(
        "[Telemetry] Data sent: machine name/id, app version, mode, performance metrics, errors."
    )
    output_func(
        "[Telemetry] Not sent: private keys, seeds, mnemonics, file contents."
    )
    output_func(
        "[Telemetry] Disable anytime with --no-telemetry or local config toggle."
    )


def _maybe_prompt_machine_name(input_func, output_func) -> str:
    if getattr(settings, "MACHINE_NAME", None):
        return get_machine_name()
    existing_name, _ = get_machine_name_state()
    if existing_name:
        return existing_name
    suggestion = suggest_machine_name()
    output_func(
        f"[Telemetry] Friendly machine name [{suggestion}]: ",
    )
    try:
        response = input_func().strip()
    except (EOFError, KeyboardInterrupt):
        response = ""
    selected = response or suggestion
    set_machine_name(selected)
    return get_machine_name()


def run_telemetry_setup(
    *,
    endpoint: str = TELEMETRY_ENDPOINT,
    interactive: bool = True,
    input_func=input,
    output_func=print,
    requests_module=requests,
    force: bool = False,
) -> TelemetrySetupOutcome:
    if not interactive:
        return TelemetrySetupOutcome(token=None, disabled=False)
    existing_token = _resolve_auth_token(None)
    if existing_token and not force:
        return TelemetrySetupOutcome(token=existing_token, disabled=False)

    _print_disclosure(output_func, endpoint)
    machine_name = _maybe_prompt_machine_name(input_func, output_func)

    while True:
        output_func("")
        output_func("[Telemetry] Setup options:")
        output_func("  [1] Paste existing token")
        output_func("  [2] Pair this machine via browser (recommended)")
        output_func("  [3] Disable telemetry")
        output_func("Select 1/2/3: ")
        try:
            choice = input_func().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return TelemetrySetupOutcome(token=None, disabled=False)

        if choice in {"3", "disable", "d"}:
            set_telemetry_opt_out(True)
            output_func("[Telemetry] Telemetry disabled locally.")
            return TelemetrySetupOutcome(token=None, disabled=True)

        if choice in {"1", "token", "t"}:
            output_func("Paste AUTH_TOKEN: ")
            try:
                token = input_func().strip()
            except (EOFError, KeyboardInterrupt):
                return TelemetrySetupOutcome(token=None, disabled=False)
            if not _valid_token_format(token):
                output_func("[Telemetry] Token format looks invalid. Expected JWT.")
                continue
            ok, machine_id, reason = _attempt_machine_registration(
                endpoint=endpoint,
                token=token,
                machine_name=machine_name,
                requests_module=requests_module,
            )
            if ok:
                persist_auth_token(token)
                set_telemetry_opt_out(False)
                if machine_id:
                    _save_machine_id(machine_id)
                output_func("[Telemetry] Token saved. Telemetry ready.")
                return TelemetrySetupOutcome(token=token, disabled=False)
            output_func(f"[Telemetry] Registration failed: {reason}")
            output_func("[Telemetry] Try again or choose another option.")
            continue

        if choice in {"2", "pair", "p"}:
            init_url = f"{_telemetry_base_url(endpoint)}/pair/init"
            try:
                response = requests_module.post(
                    init_url,
                    json={},
                    headers=_telemetry_headers(),
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 401 and not TELEMETRY_API_KEY:
                    output_func(
                        "[Telemetry] Telemetry server requires API key; set TELEMETRY_API_KEY "
                        "or use a public endpoint."
                    )
                output_func(f"[Telemetry] Pairing init failed: {exc}")
                continue
            pair_code = data.get("pair_code")
            pair_url = data.get("pair_url")
            poll_interval = int(
                data.get("poll_interval_seconds") or PAIR_POLL_DEFAULT_SECONDS
            )
            if not pair_code or not pair_url:
                output_func("[Telemetry] Pairing response missing code or URL.")
                continue
            output_func(f"Open: {pair_url} and enter code: {pair_code}")
            status_url = f"{_telemetry_base_url(endpoint)}/pair/status"
            started = time.time()
            while True:
                if time.time() - started > 300:
                    output_func("[Telemetry] Pairing timed out. Try again.")
                    break
                try:
                    status_resp = requests_module.get(
                        status_url,
                        params={"pair_code": pair_code},
                        headers=_telemetry_headers(),
                        timeout=10,
                    )
                    status_resp.raise_for_status()
                    status_data = status_resp.json()
                except Exception as exc:
                    output_func(f"[Telemetry] Pairing status error: {exc}")
                    time.sleep(poll_interval)
                    continue
                status = str(status_data.get("status") or "").lower()
                if status in {"approved", "claimed"}:
                    token = status_data.get("token")
                    if not token or not _valid_token_format(str(token)):
                        output_func("[Telemetry] Pairing approved but token invalid.")
                        break
                    ok, machine_id, reason = _attempt_machine_registration(
                        endpoint=endpoint,
                        token=str(token),
                        machine_name=machine_name,
                        requests_module=requests_module,
                    )
                    if not ok:
                        output_func(f"[Telemetry] Registration failed: {reason}")
                        break
                    persist_auth_token(str(token))
                    set_telemetry_opt_out(False)
                    if machine_id:
                        _save_machine_id(machine_id)
                    output_func("[Telemetry] Pairing complete. Telemetry ready.")
                    return TelemetrySetupOutcome(token=str(token), disabled=False)
                if status in {"denied", "expired"}:
                    output_func("[Telemetry] Pairing denied or expired.")
                    break
                time.sleep(poll_interval)
            continue

        output_func("[Telemetry] Invalid selection. Please choose 1, 2, or 3.")


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


def _normalize_range_tag(mode: Optional[str], range_id: Optional[str]) -> str:
    cleaned = str(range_id).strip() if range_id is not None else ""
    if not cleaned or cleaned == "default":
        return f"{mode}-global" if mode else "global"
    return cleaned


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
        self.machine_identity = get_machine_identity(self.hardware_machine_id)
        self.display_name = get_machine_name(self.hardware_machine_id)
        self.machine_name = self.display_name
        self.auth_token = _resolve_auth_token(auth_token)
        self._recent_ranges: deque[Dict[str, Any]] = deque(maxlen=RANGE_RECENT_LIMIT)
        self._range_lock = threading.Lock()
        self._last_range_event: Optional[Dict[str, Any]] = None
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
            try:
                range_metadata = self._current_range_metadata(mode=None, range_id=None)
                log_with_context(
                    logger,
                    "INFO",
                    "[Telemetry] Payload identity | machine_identity=%s | display_name=%s | range_tag=%s | range_start=%s | range_end=%s",
                    self.machine_identity,
                    self.display_name,
                    range_metadata.get("range_tag"),
                    range_metadata.get("range_start"),
                    range_metadata.get("range_end"),
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
        return _telemetry_headers(self.auth_token)

    def _machine_endpoints(self, machine_id: Optional[str] = None) -> tuple[str, str]:
        base = _telemetry_base_url(self.endpoint)
        register_url = f"{base}/machines/register"
        telemetry_url = (
            f"{base}/machines/{machine_id}/telemetry" if machine_id else f"{base}/machines/telemetry"
        )
        return register_url, telemetry_url

    def _snapshot_url(self, machine_id: str) -> str:
        base = _telemetry_base_url(self.endpoint)
        return f"{base}/machines/{machine_id}/snapshot"

    def _current_range_metadata(
        self,
        *,
        mode: Optional[str],
        range_id: Optional[str],
    ) -> Dict[str, Optional[Any]]:
        with self._range_lock:
            ranges = list(self._recent_ranges)
            last_range = self._last_range_event
        selected: Optional[Dict[str, Any]] = None
        if range_id:
            for entry in reversed(ranges):
                if entry.get("range_id") == range_id:
                    selected = entry
                    break
        if selected is None and last_range:
            selected = last_range
        selected_range_id = (
            range_id
            if range_id is not None
            else (selected.get("range_id") if selected else None)
        )
        return {
            "range_tag": _normalize_range_tag(mode, selected_range_id),
            "range_start": selected.get("start") if selected else None,
            "range_end": selected.get("end") if selected else None,
        }

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
                try:
                    from core.dashboard import set_metric

                    set_metric("global_run_state", "paused" if cmd == "pause" else "running")
                except Exception:
                    pass
            except Exception as exc:
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Control command failed | command=%s | reason=%s",
                        cmd,
                        exc,
                        **_telemetry_log_context(endpoint=self.endpoint),
                    )
                except Exception:
                    pass
                return False
            self._update_control_state(
                {
                    "paused": cmd == "pause",
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            try:
                log_with_context(
                    logger,
                    "INFO",
                    "[Telemetry] Control command applied | command=%s",
                    cmd,
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return True
        if cmd in {"stop", "restart"}:
            try:
                from core.dashboard import get_shutdown_event

                shutdown = get_shutdown_event()
                if shutdown:
                    shutdown.set()
                try:
                    from core.dashboard import set_metric

                    set_metric("global_run_state", "stopped")
                except Exception:
                    pass
                self._update_control_state(
                    {
                        "last_command": cmd,
                        "last_value": value,
                    }
                )
                if cmd == "restart":
                    def _restart() -> None:
                        import os
                        import sys
                        time.sleep(1)
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    threading.Thread(target=_restart, daemon=True).start()
                try:
                    log_with_context(
                        logger,
                        "INFO",
                        "[Telemetry] Control command applied | command=%s",
                        cmd,
                        **_telemetry_log_context(endpoint=self.endpoint),
                    )
                except Exception:
                    pass
                return True
            except Exception as exc:
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Control command failed | command=%s | reason=%s",
                        cmd,
                        exc,
                        **_telemetry_log_context(endpoint=self.endpoint),
                    )
                except Exception:
                    pass
                return False
        if cmd == "set_mode":
            self._update_control_state(
                {
                    "mode": value,
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            try:
                log_with_context(
                    logger,
                    "INFO",
                    "[Telemetry] Control command applied | command=%s | value=%s",
                    cmd,
                    value,
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return True
        if cmd == "set_range":
            self._update_control_state(
                {
                    "range": value,
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            try:
                log_with_context(
                    logger,
                    "INFO",
                    "[Telemetry] Control command applied | command=%s | value=%s",
                    cmd,
                    value,
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return True
        if cmd == "queue_seed":
            entries = parse_queue_value(value)
            added = enqueue_many(entries)
            _safe_set_metric("seed_queue_depth", seed_queue_size())
            self._update_control_state(
                {
                    "seed_queue_depth": seed_queue_size(),
                    "last_command": cmd,
                    "last_value": value,
                }
            )
            if added:
                try:
                    log_with_context(
                        logger,
                        "INFO",
                        "[Telemetry] Seed queue updated | added=%s",
                        added,
                        **_telemetry_log_context(endpoint=self.endpoint),
                    )
                except Exception:
                    pass
                return True
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Seed queue command ignored; no entries parsed",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
            except Exception:
                pass
            return False
        try:
            log_with_context(
                logger,
                "WARNING",
                "[Telemetry] Unknown control command | command=%s",
                cmd,
                **_telemetry_log_context(endpoint=self.endpoint),
            )
        except Exception:
            pass
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
            global _MISSING_TOKEN_LOGGED
            if not _MISSING_TOKEN_LOGGED:
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Missing AUTH_TOKEN; cannot register machine",
                        **_telemetry_log_context(endpoint=self.endpoint),
                    )
                except Exception:
                    pass
                _MISSING_TOKEN_LOGGED = True
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

    def send_snapshot(self, snapshot: MachineTelemetrySnapshot) -> bool:
        if not self._ensure_machine_registered():
            return False
        if not self.machine_id:
            return False
        url = self._snapshot_url(self.machine_id)
        range_metadata = self._current_range_metadata(
            mode=snapshot.runtime.mode,
            range_id=None,
        )
        with self._range_lock:
            recent_ranges = list(self._recent_ranges)
        payload = snapshot.dict()
        payload.update(
            {
                "range_tag": range_metadata.get("range_tag"),
                "range_start": range_metadata.get("range_start"),
                "range_end": range_metadata.get("range_end"),
                "range_recent": recent_ranges,
                "range_distribution": self._range_distribution(recent_ranges),
            }
        )
        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._authorization_headers(),
                timeout=10,
            )
            response.raise_for_status()
            try:
                log_with_context(
                    logger,
                    "INFO",
                    "[Telemetry] Snapshot uploaded | machine_id=%s status=%s",
                    self.machine_id,
                    response.status_code,
                    **_telemetry_log_context(endpoint=url),
                )
            except Exception:
                pass
            return True
        except Exception as exc:
            try:
                log_with_context(
                    logger,
                    "WARNING",
                    "[Telemetry] Snapshot upload failed | reason=%s",
                    getattr(getattr(exc, "response", None), "status_code", exc),
                    **_telemetry_log_context(endpoint=url),
                )
            except Exception:
                pass
            return False

    def start_snapshot_loop(self, shutdown_event: threading.Event) -> None:
        if getattr(self, "_snapshot_thread", None) is not None:
            if self._snapshot_thread.is_alive():  # type: ignore[attr-defined]
                return
        if TELEMETRY_SNAPSHOT_SECONDS <= 0:
            return

        def _loop() -> None:
            from core.dashboard import get_current_metrics

            while not shutdown_event.is_set():
                try:
                    if not self._ensure_machine_registered():
                        shutdown_event.wait(TELEMETRY_SNAPSHOT_SECONDS)
                        continue
                    metrics = get_current_metrics()
                    recent_ranges = self._collect_recent_ranges()
                    snapshot = _snapshot_from_metrics(
                        metrics,
                        machine_id=self.machine_id or "",
                        machine_name=self.machine_name,
                        machine_identity=self.machine_identity,
                        display_name=self.display_name,
                        app_instance_id=self.app_id,
                        client_version=CLIENT_VERSION,
                        recent_ranges=recent_ranges,
                    )
                    self.send_snapshot(snapshot)
                except Exception:
                    pass
                shutdown_event.wait(TELEMETRY_SNAPSHOT_SECONDS)

        if can_spawn_thread("telemetry_snapshot"):
            self._snapshot_thread = threading.Thread(
                target=_loop,
                name="telemetry_snapshot",
                daemon=True,
            )
            self._snapshot_thread.start()
        else:
            logger.warning("[Telemetry] Skipping snapshot thread; at thread limit")

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
    ) -> Optional[Dict[str, Any]]:
        """Record a bounded recent range observation for telemetry payloads."""

        if not self.enabled:
            return None

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
            self._last_range_event = payload
        return payload

    def _range_distribution(self, ranges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return build_range_distribution(ranges)

    def _collect_recent_ranges(self) -> List[Dict[str, Any]]:
        with self._range_lock:
            return list(self._recent_ranges)

    def record_event(
        self,
        seed_bytes: bytes,
        *,
        mode: str,
        range_id: Optional[str],
        used: bool,
        match_found: bool,
        range_observation: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a telemetry event to the queue."""

        if not self.enabled:
            return

        fingerprint = hashlib.sha256(seed_bytes + self.app_id.encode()).hexdigest()
        with self._range_lock:
            recent_ranges = list(self._recent_ranges)
        range_metadata = self._current_range_metadata(mode=mode, range_id=range_id)
        distribution_source = [range_observation] if range_observation else None
        range_distribution = (
            build_range_distribution(distribution_source) if distribution_source else None
        )
        payload = {
            "app_instance_id": self.app_id,
            "client_version": CLIENT_VERSION,
            "mode": mode,
            "range_id": range_id,
            "range_tag": range_metadata.get("range_tag"),
            "range_start": range_metadata.get("range_start"),
            "range_end": range_metadata.get("range_end"),
            "seed_fingerprint": fingerprint,
            "timestamp_iso": datetime.utcnow().isoformat() + "Z",
            "used": used,
            "match_found": match_found,
            "machine_id": self.machine_id,
            "machine_name": self.machine_name,
            "machine_identity": self.machine_identity,
            "display_name": self.display_name,
            "range_recent": recent_ranges,
            "range_distribution": range_distribution,
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
            enriched.setdefault(
                "machine_name",
                self.machine_name or get_machine_name(self.hardware_machine_id),
            )
            enriched.setdefault("machine_identity", self.machine_identity)
            enriched.setdefault("display_name", self.display_name or self.machine_name)
            range_metadata = self._current_range_metadata(
                mode=enriched.get("mode"),
                range_id=enriched.get("range_id"),
            )
            if not enriched.get("range_tag") or enriched.get("range_tag") == "default":
                enriched["range_tag"] = range_metadata.get("range_tag")
            if enriched.get("range_start") is None:
                enriched["range_start"] = range_metadata.get("range_start")
            if enriched.get("range_end") is None:
                enriched["range_end"] = range_metadata.get("range_end")
            enriched.setdefault("range_recent", [])
            enriched.setdefault("range_distribution", [])
            payload.append(enriched)
        if payload:
            try:
                log_with_context(
                    logger,
                    "DEBUG",
                    "[Telemetry] Payload sample | size=%s | first=%s",
                    len(payload),
                    payload[0],
                    **_telemetry_log_context(endpoint=telemetry_url),
                )
            except Exception:
                pass
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
                _safe_set_metric("telemetry_flush_rate", 0)
                return True
            ids, payloads = zip(*batch)
            try:
                log_with_context(
                    logger,
                    "INFO",
                    f"[Telemetry] Flushing {len(ids)} event(s) to {self.endpoint}",
                    **_telemetry_log_context(endpoint=self.endpoint),
                )
                started_at = time.perf_counter()
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
                _safe_set_metric("telemetry_flush_rate", 0)
                return False
            with conn:
                conn.execute(
                    f"DELETE FROM telemetry WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
            self._backoff = self.flush_seconds
            duration = max(time.perf_counter() - started_at, 0.001)
            _safe_set_metric("telemetry_flush_rate", round(len(ids) / duration, 2))
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
_CLIENT_LOCK = threading.Lock()
_SEED_CLIENT_SHUTDOWN_EVENT: Optional[threading.Event] = None


def _is_main_process() -> bool:
    try:
        return multiprocessing.current_process().name == "MainProcess"
    except Exception:
        return True


def _ensure_seed_client() -> Optional[TelemetryClient]:
    """Ensure a telemetry client exists for seed events in this process."""

    if _CLIENT is not None:
        return _CLIENT
    if not SEED_TELEMETRY_ENABLED or telemetry_opted_out():
        return None
    auth_token = _resolve_auth_token(None)
    if not auth_token:
        return None
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        db_path = QUEUE_DB
        if not _is_main_process():
            db_path = Path(LOG_DIR) / f"telemetry_queue_{os.getpid()}.db"
        client = TelemetryClient(auth_token=auth_token, db_path=db_path)
        global _SEED_CLIENT_SHUTDOWN_EVENT
        if _SEED_CLIENT_SHUTDOWN_EVENT is None:
            _SEED_CLIENT_SHUTDOWN_EVENT = threading.Event()
        client.start(_SEED_CLIENT_SHUTDOWN_EVENT)
        _CLIENT = client
        return _CLIENT


def start_telemetry(
    shutdown_event: threading.Event,
    *,
    interactive: Optional[bool] = None,
    force_setup: bool = False,
) -> None:
    """Initialize and start the global telemetry client."""

    if not SEED_TELEMETRY_ENABLED:
        return
    if telemetry_opted_out() and not force_setup:
        return

    if interactive is None:
        interactive = _is_interactive()
    else:
        interactive = bool(interactive) and _is_interactive()

    auth_token = _resolve_auth_token(None)
    if force_setup and interactive:
        run_telemetry_setup(endpoint=TELEMETRY_ENDPOINT, interactive=True, force=True)
        auth_token = _resolve_auth_token(None)

    if not auth_token:
        if interactive:
            outcome = run_telemetry_setup(endpoint=TELEMETRY_ENDPOINT, interactive=True)
            if outcome.disabled:
                return
            auth_token = _resolve_auth_token(None)
        else:
            global _MISSING_TOKEN_LOGGED
            if not _MISSING_TOKEN_LOGGED:
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Missing AUTH_TOKEN; telemetry disabled. Run `python main.py --telemetry-setup` or set AUTH_TOKEN.",
                        **_telemetry_log_context(endpoint=TELEMETRY_ENDPOINT),
                    )
                except Exception:
                    pass
                _MISSING_TOKEN_LOGGED = True
            return
    if not auth_token:
        return

    validation = _validate_auth_token(
        endpoint=TELEMETRY_ENDPOINT,
        token=auth_token,
    )
    if validation is False:
        clear_persisted_auth_token()
        if interactive:
            outcome = run_telemetry_setup(
                endpoint=TELEMETRY_ENDPOINT,
                interactive=True,
                force=True,
            )
            if outcome.disabled:
                return
            auth_token = _resolve_auth_token(None)
        else:
            global _INVALID_TOKEN_LOGGED
            if not _INVALID_TOKEN_LOGGED:
                try:
                    log_with_context(
                        logger,
                        "WARNING",
                        "[Telemetry] Invalid AUTH_TOKEN; telemetry disabled. Run `python main.py --telemetry-setup` or set AUTH_TOKEN.",
                        **_telemetry_log_context(endpoint=TELEMETRY_ENDPOINT),
                    )
                except Exception:
                    pass
                _INVALID_TOKEN_LOGGED = True
            return
    if not auth_token:
        return

    global _CLIENT
    if _CLIENT is None:
        _CLIENT = TelemetryClient(auth_token=auth_token)
    _CLIENT.start(shutdown_event)
    _CLIENT.start_control_polling(shutdown_event)
    _CLIENT.start_snapshot_loop(shutdown_event)
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
        auth_token = _CLIENT.auth_token if _CLIENT else _resolve_auth_token(None)
        headers = _telemetry_headers(auth_token)
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
    range_observation: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a telemetry event if the global client is active."""

    client = _ensure_seed_client()
    if client is None:
        return
    client.record_event(
        seed_bytes,
        mode=mode,
        range_id=range_id,
        used=used,
        match_found=match_found,
        range_observation=range_observation,
    )


def record_range_event(
    *,
    mode: str,
    range_id: str,
    start: int,
    end: int,
    space_min: Optional[int] = None,
    space_max: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Record a range observation for telemetry payload enrichment."""

    client = _ensure_seed_client()
    if client is None:
        return None
    return client.record_range_event(
        mode=mode,
        range_id=range_id,
        start=start,
        end=end,
        space_min=space_min,
        space_max=space_max,
    )
