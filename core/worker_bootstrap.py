import time

try:
    from core.dashboard import init_shared_metrics, set_metric, increment_metric, log_message
except Exception:
    # Fallback shims if dashboard import fails very early
    def init_shared_metrics(): return None
    def set_metric(*_a, **_k): return None
    def increment_metric(*_a, **_k): return None
    def log_message(msg, level="INFO"): print(f"[{level}] {msg}")

_metrics_ready = {"ok": False}

def ensure_metrics_ready():
    """Idempotently initialize metrics in THIS process and write a heartbeat."""
    if _metrics_ready["ok"]:
        return True
    try:
        init_shared_metrics()               # safe if already inited elsewhere
        set_metric("_worker_heartbeat", int(time.time()))
        _metrics_ready["ok"] = True
        log_message("[worker_bootstrap] Shared metrics initialized", "DEBUG")
        return True
    except Exception as e:
        log_message(f"[worker_bootstrap] Metrics not ready: {e}", "WARNING")
        return False

def _safe_set_metric(name, value):
    try:
        if not _metrics_ready["ok"]:
            ensure_metrics_ready()
        set_metric(name, value)
    except Exception:
        # swallow—workers should never crash on metrics
        pass

def _safe_inc_metric(name, amount=1):
    try:
        if not _metrics_ready["ok"]:
            ensure_metrics_ready()
        increment_metric(name, amount)
    except Exception:
        pass
