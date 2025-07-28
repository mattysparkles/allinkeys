import threading
import time
import json
import os
from datetime import datetime, timezone, timedelta
import core.checkpoint as checkpoint
import traceback
import multiprocessing
from config.settings import (
    ENABLE_KEYGEN,
    ENABLE_DAY_ONE_CHECK,
    ENABLE_UNIQUE_RECHECK,
    ENABLE_ALTCOIN_DERIVATION,
    ENABLE_BACKLOG_CONVERSION,
    ENABLE_ALERTS,
    FILES_PER_BATCH,
    ENABLE_AUTO_TIMEZONE_SETTING,
    MANUAL_TIME_ZONE_OVERRIDE,
)

# Thread health tracking (expanded)
THREAD_HEALTH = {
    "keygen": ENABLE_KEYGEN,
    "csv_check": ENABLE_DAY_ONE_CHECK,
    "csv_recheck": ENABLE_UNIQUE_RECHECK,
    "backlog": ENABLE_BACKLOG_CONVERSION,
    "altcoin": ENABLE_ALTCOIN_DERIVATION,
    "dashboard": True,
    "alerts": ENABLE_ALERTS,
}

# Control events propagated from the main process
shutdown_event = None
pause_event = None
module_pause_events = {}
module_shutdown_events = {}

# Alert channels mirrored from core.alerts to avoid circular import
ALERT_CHANNELS = [
    "email",
    "telegram",
    "popup",
    "sms",
    "file",
    "cloud",
    "phone",
    "discord",
    "webhook",
    "home_assistant",
]

# Lifetime metrics persistence
METRICS_LIFETIME_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'metrics_lifetime.json'))
LIFETIME_KEYS = {
    'keys_generated_lifetime',
    'csv_checked_lifetime',
    'csv_rechecked_lifetime',
    'csv_created_lifetime',
    'matches_found_lifetime',
    'addresses_checked_lifetime',
    'addresses_generated_lifetime',
    'alerts_sent_lifetime',
    'lifetime_start_timestamp',
}

# Simple helpers for modules to report alive/dead status
def set_thread_health(name, running: bool):
    THREAD_HEALTH[name] = running
    update_dashboard_stat("thread_health_flags", THREAD_HEALTH.copy())


def register_control_events(shutdown, pause, module=None):
    """Register shutdown and pause events for a specific module or globally."""
    global shutdown_event, pause_event
    if module:
        module_shutdown_events[module] = shutdown
        module_pause_events[module] = pause
    else:
        shutdown_event = shutdown
        pause_event = pause


def get_shutdown_event(module=None):
    if module and module in module_shutdown_events:
        return module_shutdown_events[module]
    return shutdown_event


def get_pause_event(module=None):
    if module and module in module_pause_events:
        return module_pause_events[module]
    return pause_event

# Delayed initialization of Manager and Lock to avoid multiprocessing import issues on Windows
manager = None
metrics_lock = None
metrics = None


def load_lifetime_metrics():
    """Load persisted lifetime metrics from disk."""
    if not os.path.exists(METRICS_LIFETIME_PATH):
        return {}
    try:
        with open(METRICS_LIFETIME_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if 'lifetime_start_timestamp' not in data:
                data['lifetime_start_timestamp'] = datetime.utcnow().isoformat()
            return data
    except Exception:
        return {}


def save_lifetime_metrics():
    """Persist lifetime metrics to disk."""
    if metrics is None:
        return
    data = {}
    for key in LIFETIME_KEYS:
        val = metrics.get(key)
        if isinstance(val, dict):
            data[key] = dict(val)
        else:
            data[key] = val
    try:
        # [FIX PHASE 2] atomic write to survive crashes/power loss
        tmp_path = METRICS_LIFETIME_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, METRICS_LIFETIME_PATH)
    except Exception:
        pass


def maybe_persist_lifetime(key):
    """Persist metrics if the key belongs to lifetime stats."""
    base = key.split('.')[0] if isinstance(key, str) else key
    if base in LIFETIME_KEYS:
        save_lifetime_metrics()


def get_local_timezone():
    """Return the configured timezone object."""
    if ENABLE_AUTO_TIMEZONE_SETTING:
        try:
            from tzlocal import get_localzone
            return get_localzone()
        except Exception:
            pass
    # Fallback manual offset like "UTC-5"
    try:
        if MANUAL_TIME_ZONE_OVERRIDE.startswith('UTC'):
            sign = -1 if '-' in MANUAL_TIME_ZONE_OVERRIDE else 1
            hours = int(MANUAL_TIME_ZONE_OVERRIDE.split('UTC')[1].replace('+','').replace('-',''))
            return timezone(timedelta(hours=sign * hours))
    except Exception:
        pass
    return timezone.utc


TODAY_METRIC_KEYS = [
    'csv_checked_today',
    'csv_rechecked_today',
    'csv_created_today',
    'keys_generated_today',
    'derived_addresses_today',
    'altcoin_files_converted',
    'alerts_sent_today',
]
TODAY_METRIC_KEYS += [f'addresses_checked_today', f'addresses_generated_today', f'matches_found_today']


def reset_daily_metrics_if_needed():
    tz = get_local_timezone()
    last_str = get_metric('metrics_last_reset')
    try:
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        last = last.astimezone(tz)
    except Exception:
        last = datetime.now(tz)
    now = datetime.now(tz)
    if now.date() != last.date():
        for key in TODAY_METRIC_KEYS:
            val = get_metric(key)
            if isinstance(val, dict):
                update_dashboard_stat(key, {k: 0 for k in val})
            else:
                update_dashboard_stat(key, 0)
        update_dashboard_stat('metrics_last_reset', now.isoformat())
        save_lifetime_metrics()


def init_dashboard_manager():
    """
    Initializes the multiprocessing manager and shared metric dictionary.
    This must be called from inside `if __name__ == "__main__"` block.
    """
    global manager, metrics, metrics_lock
    if manager is None:
        manager = multiprocessing.Manager()
        metrics_lock = manager.Lock()
        default_metrics = _default_metrics()
        metrics = manager.dict()
        metrics.update({k: (manager.dict(v) if isinstance(v, dict) else v)
                        for k, v in default_metrics.items()})
        # Load persisted lifetime values
        lifetime = load_lifetime_metrics()
        for k, v in lifetime.items():
            if isinstance(v, dict):
                metrics[k] = manager.dict(v)
            else:
                metrics[k] = v
    return metrics


def _default_metrics():
    return {
        "batches_completed": 0,
        "current_seed_index": 0,
        "vanitysearch_speed": "0 MKeys/s",
        "keys_per_sec": 0,
        "active_gpus": {},
        "csv_checked_today": 0,
        "csv_checked_lifetime": 0,
        "uptime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "processed_pages": 0,
        "checked_pages": 0,
        "matched_keys": 0,
        "batches_completed": 0,
        "keys_generated_today": 0,
        "addresses_generated_today": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "keys_generated_lifetime": 0,
        "addresses_generated_lifetime": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "csv_created_today": 0,
        "csv_created_lifetime": 0,
        "csv_rechecked_today": 0,
        "csv_rechecked_lifetime": 0,
        "derived_addresses_today": 0,
        "altcoin_files_converted": 0,
        "alerts_sent_today": {c: 0 for c in ALERT_CHANNELS},
        "alerts_sent_lifetime": {c: 0 for c in ALERT_CHANNELS},
        "addresses_checked_today": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "addresses_checked_lifetime": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "matches_found_today": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "matches_found_lifetime": {
            "btc": 0, "doge": 0, "ltc": 0, "bch": 0, "rvn": 0, "pep": 0, "dash": 0, "eth": 0
        },
        "lifetime_start_timestamp": datetime.utcnow().isoformat(),
        "avg_keygen_time": 0,
        "avg_check_time": 0,
        "disk_free_gb": 0,
        "disk_fill_eta": "N/A",
        "cpu_usage": "0%",
        "ram_usage": "0 GB / 0 GB (0%)",
        "gpu_stats": {},
        "gpu_assignments": {"vanitysearch": "N/A", "altcoin_derive": "N/A"},
        "state": "Initializing",
        "active_processes": [],
        "csv_check_queue": [],
        "csv_recheck_queue": [],
        "csv_check_progress": {},
        "csv_recheck_progress": {},
        "vanity_last_batch_id": 0,
        "vanity_current_batch_id": 0,
        "vanity_next_batch_id": 0,
        "vanity_batch_size": FILES_PER_BATCH,
        "vanity_progress_percent": 0,
        "vanity_max_file_size_mb": 500,
        "vanity_custom_flags": "",
        "backlog_files_queued": 0,
        "backlog_eta": "N/A",
        "backlog_avg_time": "N/A",
        "backlog_current_file": "",
        "backlog_progress": {},
        "csv_checker": {
            "rows_checked": 0,
            "matches_found": 0,
            "last_file": "",
            "last_timestamp": "",
        },
        # Human readable module state. Thread health flags expose booleans for
        # programmatic checks so this can safely store strings for the GUI.
        "status": {
            "keygen": "Running" if ENABLE_KEYGEN else "Stopped",
            "altcoin": "Running" if ENABLE_ALTCOIN_DERIVATION else "Stopped",
            "csv_check": "Running" if ENABLE_DAY_ONE_CHECK else "Stopped",
            "csv_recheck": "Running" if ENABLE_UNIQUE_RECHECK else "Stopped",
            "backlog": "Running" if ENABLE_BACKLOG_CONVERSION else "Stopped",
            "alerts": "Running" if ENABLE_ALERTS else "Stopped",
        },
        "global_run_state": "running",
        "auto_resume_enabled": True,
        "alerts_enabled": {
            "email": False,
            "telegram": False,
            "popup": True,
            "sms": False,
            "file": True,
            "cloud": False,
            "phone": False,
            "discord": False,
            "webhook": False,
            "home_assistant": False
        },
        "pin_reset_required": False,
        "metrics_last_reset": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "thread_health_flags": THREAD_HEALTH.copy()
    }


def init_shared_metrics(shared_dict):
    """Replace internal metrics dict with externally provided manager dict."""
    global metrics
    if shared_dict is not None:
        metrics = shared_dict


def update_dashboard_stat(key, value=None, retries=5, delay=0.2):
    global metrics
    for attempt in range(retries):
        if metrics is None:
            if attempt == retries - 1:
                print(f"⚠️ update_dashboard_stat skipped after {retries} tries: metrics is None (key={key})", flush=True)
                return
            time.sleep(delay)
        else:
            break
    try:
        if metrics_lock:
            with metrics_lock:
                _update_stat_internal(key, value)
        else:
            _update_stat_internal(key, value)
    except Exception as e:
        print(f"❌ update_dashboard_stat failed on key: {key} | {e}", flush=True)
        traceback.print_exc()


def _update_stat_internal(key, value=None):
    global metrics
    if metrics is None:
        print(f"⚠️ _update_stat_internal skipped: metrics is None (key={key})", flush=True)
        return

    if isinstance(key, dict) and value is None:
        for k, v in key.items():
            _update_stat_internal(k, v)
        return

    if value is None:
        print(
            f"⚠️ update_dashboard_stat('{key}') called without a value. Defaulting to 'N/A'",
            flush=True,
        )
        value = "N/A"

    # Support dotted keys for nested dict updates
    if isinstance(key, str) and "." in key:
        top, sub = key.split(".", 1)
        if isinstance(metrics.get(top), dict):
            metrics[top][sub] = value
            return

    metrics[key] = value
    maybe_persist_lifetime(key)

def increment_metric(key, amount=1):
    """Increase a metric value in a process-safe manner."""
    if metrics_lock:
        with metrics_lock:
            if "." in key:
                top, sub = key.split(".", 1)
                if isinstance(metrics.get(top), dict):
                    metrics[top][sub] = metrics[top].get(sub, 0) + amount
            elif isinstance(metrics.get(key), int):
                metrics[key] += amount
    else:
        if "." in key:
            top, sub = key.split(".", 1)
            if isinstance(metrics.get(top), dict):
                metrics[top][sub] = metrics[top].get(sub, 0) + amount
        elif isinstance(metrics.get(key), int):
            metrics[key] = metrics.get(key, 0) + amount
    maybe_persist_lifetime(key)


def set_metric(key, value):
    """Convenience wrapper for updating a single metric key."""
    dict_expected = {
        "addresses_generated_lifetime",
        "addresses_checked_lifetime",
        "matches_found_lifetime",
        "addresses_generated_today",
        "addresses_checked_today",
        "matches_found_today",
    }
    base = key.split(".", 1)[0] if isinstance(key, str) else key
    if base in dict_expected and not isinstance(value, dict):
        print(
            f"[dashboard] Warning: expected dict for {base}, got {type(value).__name__}",
            flush=True,
        )
        return
    update_dashboard_stat(key, value)


def get_metric(key, default=None):
    """Retrieve a metric value in a threadsafe way."""
    global metrics
    if metrics is None:
        return default
    if metrics_lock:
        with metrics_lock:
            if "." in key:
                top, sub = key.split(".", 1)
                if isinstance(metrics.get(top), dict):
                    return metrics[top].get(sub, default)
            return metrics.get(key, default)
    else:
        if "." in key:
            top, sub = key.split(".", 1)
            if isinstance(metrics.get(top), dict):
                return metrics[top].get(sub, default)
        return metrics.get(key, default)


def _to_plain(obj):
    """Recursively convert Manager proxy objects to built-in types."""
    try:
        from multiprocessing.managers import BaseProxy
    except Exception:  # pragma: no cover - platform dependent
        BaseProxy = object
    if isinstance(obj, BaseProxy):
        try:
            obj = obj._getvalue()  # type: ignore[attr-defined]
        except Exception:
            obj = dict(obj) if hasattr(obj, "items") else list(obj)
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return type(obj)(_to_plain(v) for v in obj)
    return obj

def get_current_metrics():
    """Return a snapshot of the shared metrics dict.

    Previously this returned an empty dict when ``metrics_lock`` was ``None``.
    In practice the lock is not always created when an external manager is
    supplied via :func:`init_shared_metrics`.  The dashboard GUI relies on this
    function to retrieve live stats, so returning ``{}`` resulted in every
    metric showing "N/A".  We now gracefully handle the no-lock case and still
    return the current metrics.
    """
    global metrics
    if metrics is None:
        return {}
    if metrics_lock:
        with metrics_lock:
            metrics["thread_health_flags"] = THREAD_HEALTH.copy()
            snapshot = dict(metrics)
    else:
        metrics["thread_health_flags"] = THREAD_HEALTH.copy()
        snapshot = dict(metrics)
    return _to_plain(snapshot)


def load_checkpoint_file(filepath=None):
    return checkpoint.load_keygen_checkpoint()


def reset_all_metrics():
    if not metrics_lock:
        return
    with metrics_lock:
        for key in metrics:
            if isinstance(metrics[key], (int, float)):
                metrics[key] = 0
            elif isinstance(metrics[key], list):
                metrics[key] = []
            elif isinstance(metrics[key], dict):
                metrics[key] = {k: 0 for k in metrics[key]}
            else:
                metrics[key] = None
        metrics["state"] = "Reset"
        metrics["uptime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        metrics["metrics_last_reset"] = datetime.now().isoformat()
        metrics["lifetime_start_timestamp"] = datetime.utcnow().isoformat()
    if os.path.exists(METRICS_LIFETIME_PATH):
        try:
            os.remove(METRICS_LIFETIME_PATH)
        except Exception:
            pass

# [FIX PHASE 2] allow clearing only lifetime stats without touching daily data
def reset_lifetime_metrics():
    if not metrics_lock:
        return
    with metrics_lock:
        for key in LIFETIME_KEYS:
            val = metrics.get(key)
            if isinstance(val, dict):
                metrics[key] = {k: 0 for k in val}
            elif isinstance(val, (int, float)):
                metrics[key] = 0
        metrics["lifetime_start_timestamp"] = datetime.utcnow().isoformat()
    if os.path.exists(METRICS_LIFETIME_PATH):
        try:
            os.remove(METRICS_LIFETIME_PATH)
        except Exception:
            pass
