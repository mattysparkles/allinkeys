import threading
import time
from datetime import datetime
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

# Simple helpers for modules to report alive/dead status
def set_thread_health(name, running: bool):
    THREAD_HEALTH[name] = running
    update_dashboard_stat("thread_health_flags", THREAD_HEALTH.copy())


def register_control_events(shutdown, pause):
    global shutdown_event, pause_event
    shutdown_event = shutdown
    pause_event = pause


def get_shutdown_event():
    return shutdown_event


def get_pause_event():
    return pause_event

# Delayed initialization of Manager and Lock to avoid multiprocessing import issues on Windows
manager = None
metrics_lock = None
metrics = None


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
        # Wrap nested dictionaries
        metrics.update({k: (manager.dict(v) if isinstance(v, dict) else v)
                        for k, v in default_metrics.items()})
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
        "status": {
            "keygen": ENABLE_KEYGEN,
            "altcoin": ENABLE_ALTCOIN_DERIVATION,
            "csv_check": ENABLE_DAY_ONE_CHECK,
            "csv_recheck": ENABLE_UNIQUE_RECHECK,
            "backlog": ENABLE_BACKLOG_CONVERSION,
            "alerts": ENABLE_ALERTS,
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

def increment_metric(key, amount=1):
    if not metrics_lock:
        return
    with metrics_lock:
        if "." in key:
            top, sub = key.split(".", 1)
            if isinstance(metrics.get(top), dict):
                metrics[top][sub] = metrics[top].get(sub, 0) + amount
        elif isinstance(metrics.get(key), int):
            metrics[key] += amount


def set_metric(key, value):
    """Convenience wrapper for updating a single metric key."""
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
            return dict(metrics)
    else:
        metrics["thread_health_flags"] = THREAD_HEALTH.copy()
        return dict(metrics)


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
