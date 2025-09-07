import subprocess
import time
from typing import Sequence
from multiprocessing import Process


def popen_with_retry(
    cmd: Sequence[str],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    module: str = "general",
    **kwargs,
) -> subprocess.Popen:
    """Launch a subprocess with retry and exponential backoff.

    Parameters
    ----------
    cmd: Sequence[str]
        Command and arguments for ``subprocess.Popen``.
    attempts: int
        Number of attempts before giving up. Defaults to 3.
    base_delay: float
        Initial delay in seconds for exponential backoff. Defaults to 1.0.
    module: str
        Module name for dashboard metrics.
    **kwargs:
        Extra keyword arguments forwarded to ``subprocess.Popen``.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return subprocess.Popen(cmd, **kwargs)
        except Exception as exc:  # pragma: no cover - rare failure
            last_exc = exc
            try:
                from core.dashboard import increment_metric, update_dashboard_stat

                increment_metric(f"popen_failures.{module}")
                update_dashboard_stat(
                    f"last_popen_error.{module}", str(exc)
                )
            except Exception:
                pass
            if attempt == attempts:
                if last_exc:
                    raise last_exc
            time.sleep(base_delay * (2 ** (attempt - 1)))
    if last_exc:
        raise last_exc


def start_process_with_retry(
    proc: Process,
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    module: str = "general",
) -> Process:
    """Start a ``multiprocessing.Process`` with retry/backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            proc.start()
            return proc
        except Exception as exc:  # pragma: no cover - rare failure
            last_exc = exc
            try:
                from core.dashboard import increment_metric, update_dashboard_stat

                increment_metric(f"popen_failures.{module}")
                update_dashboard_stat(
                    f"last_popen_error.{module}", str(exc)
                )
            except Exception:
                pass
            if attempt == attempts:
                if last_exc:
                    raise last_exc
            time.sleep(base_delay * (2 ** (attempt - 1)))
    if last_exc:
        raise last_exc
    return proc
