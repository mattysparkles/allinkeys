import time

from core.logger import get_logger

logger = get_logger(__name__)

try:
    # ``importlib.metadata`` is stdlib in 3.8+, but fall back if needed
    from importlib.metadata import entry_points
except Exception:  # pragma: no cover - very old Python
    from importlib_metadata import entry_points  # type: ignore

try:
    from core.dashboard import init_shared_metrics, set_metric, increment_metric
except Exception:
    # Fallback shims if dashboard import fails very early
    def init_shared_metrics(): return None
    def set_metric(*_a, **_k): return None
    def increment_metric(*_a, **_k): return None

_metrics_ready = {"ok": False}
_plugins_loaded = False


def ensure_metrics_ready(shared_dict=None):
    """Idempotently initialize metrics in THIS process and write a heartbeat."""
    if _metrics_ready["ok"]:
        return True
    try:
        init_shared_metrics(shared_dict)  # safe if already inited elsewhere
        set_metric("_worker_heartbeat", int(time.time()))
        _metrics_ready["ok"] = True
        logger.debug("[worker_bootstrap] Shared metrics initialized")
        return True
    except Exception as e:
        logger.warning(f"[worker_bootstrap] Metrics not ready: {e}")
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


def load_plugins():
    """Dynamically load derivation and alert plugins via entry points.

    This uses the ``allinkeys.plugins.derivation`` and
    ``allinkeys.plugins.alert`` entry point groups. Plugins are expected to
    register themselves with the framework when imported.
    """

    global _plugins_loaded
    if _plugins_loaded:
        return

    def _load_group(group: str):
        try:
            eps = entry_points()  # type: ignore[arg-type]
            # ``select`` is available on Python 3.10+; fall back otherwise
            try:
                selected = eps.select(group=group)
            except Exception:  # pragma: no cover - py3.8/3.9
                selected = [ep for ep in eps.get(group, [])]
            for ep in selected:
                try:
                    loaded = ep.load()
                    if callable(loaded):
                        loaded()
                    log_message(
                        f"[worker_bootstrap] Loaded plugin {ep.name} from {group}",
                        "DEBUG",
                    )
                except Exception as exc:  # pragma: no cover - plugin failure
                    log_message(f"[worker_bootstrap] Failed loading {ep.name}: {exc}", "ERROR")
        except Exception as exc:  # pragma: no cover - entry point failure
            log_message(f"[worker_bootstrap] Plugin discovery failed for {group}: {exc}", "ERROR")

    _load_group("allinkeys.plugins.derivation")
    _load_group("allinkeys.plugins.alert")
    _plugins_loaded = True


# Load plugins on import so they are available in all workers
load_plugins()
