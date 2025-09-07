import subprocess
import multiprocessing
import time
from typing import Iterable, Optional, Mapping, Any

from core.dashboard import increment_metric, update_dashboard_stat


def _record_failure(module: str, error: Exception) -> None:
    """Record a process launch failure for ``module``."""
    increment_metric(f"process_failures.{module}")
    update_dashboard_stat(f"process_last_error.{module}", str(error))


def popen_with_retry(
    cmd: Iterable[str],
    *,
    module: str,
    retries: int = 3,
    backoff: float = 0.5,
    **kwargs: Any,
) -> subprocess.Popen:
    """Launch a subprocess with retries and exponential backoff."""
    attempt = 0
    while True:
        try:
            return subprocess.Popen(cmd, **kwargs)
        except Exception as exc:  # pragma: no cover - rare
            _record_failure(module, exc)
            attempt += 1
            if attempt >= retries:
                raise
            time.sleep(backoff)
            backoff *= 2


def start_process_with_retry(
    *,
    module: str,
    target,
    args: tuple = (),
    kwargs: Optional[Mapping[str, Any]] = None,
    name: Optional[str] = None,
    daemon: Optional[bool] = None,
    retries: int = 3,
    backoff: float = 0.5,
) -> multiprocessing.Process:
    """Start a ``multiprocessing.Process`` with retries."""
    attempt = 0
    while True:
        try:
            p = multiprocessing.Process(target=target, args=args, kwargs=kwargs or {}, name=name)
            if daemon is not None:
                p.daemon = daemon
            p.start()
            return p
        except Exception as exc:  # pragma: no cover - rare
            _record_failure(module, exc)
            attempt += 1
            if attempt >= retries:
                raise
            time.sleep(backoff)
            backoff *= 2
