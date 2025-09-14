"""Helpers for smoothing and displaying dashboard metrics.

This module implements a small toolkit used by the GUI dashboard. It provides
three main utilities:

``KPSWindow``
    Tracks a running keys-per-second rate using either a rolling window or an
    exponential weighted moving average (EWMA) with ``alpha=0.3``.

``ModeAwareMetricCache``
    Keeps the last non-zero reading for metrics and renders those irrelevant to
    the current operating mode as ``"N/A"`` so the GUI can grey them out.

``CPUPercent``
    Samples process CPU usage without blocking, caching the last non-zero value
    to prevent the common flicker to ``0`` when sampling ``psutil``.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Tuple

import psutil


class KPSWindow:
    """Compute a smoothed keys-per-second value.

    The window keeps recent ``(timestamp, total_keys)`` samples. The raw rate is
    derived from the oldest and newest sample that fall within the window. If
    ``use_ewma`` is True an exponential weighted moving average is applied with
    factor ``alpha`` (default 0.3). Otherwise a simple rolling-window average is
    returned. The last non-zero value is cached so callers can avoid transient
    ``0`` flicker when the underlying metric momentarily pauses.
    """

    def __init__(
        self,
        window_seconds: int = 10,
        *,
        use_ewma: bool = True,
        alpha: float = 0.3,
    ) -> None:
        self.window_seconds = window_seconds
        self.use_ewma = use_ewma
        self.alpha = alpha
        self.history: Deque[Tuple[float, float]] = deque()
        self._ewma: float | None = None
        self._last_non_zero = 0.0

    def update(self, total_keys: float, timestamp: float | None = None) -> float:
        ts = time.time() if timestamp is None else timestamp
        self.history.append((ts, float(total_keys)))
        # Discard samples outside the rolling window
        while self.history and ts - self.history[0][0] > self.window_seconds:
            self.history.popleft()

        rate = 0.0
        if len(self.history) >= 2:
            t0, c0 = self.history[0]
            t1, c1 = self.history[-1]
            dt = t1 - t0
            if dt > 0:
                rate = (c1 - c0) / dt

        if self.use_ewma:
            if self._ewma is None:
                self._ewma = rate
            else:
                self._ewma = self.alpha * rate + (1 - self.alpha) * self._ewma
            rate = self._ewma

        if rate > 0:
            self._last_non_zero = rate
        else:
            rate = self._last_non_zero
        return rate


MODE_METRICS: Dict[str, set[str]] = {
    "btc": {"compressed_kps", "uncompressed_kps", "rotations", "backlog"},
    "mnemonic": {"mnemonic_sec", "derivations_sec", "gpu_ids", "rotations"},
    "puzzle": {
        "in_range_seeds_sec",
        "range_coverage_pct",
        "out_of_range_skipped",
        "restarts",
        "recoveries",
    },
    "altcoin_derive": {
        "derive_kps",
        "rows_sec",
        "current_file",
        "last_rotation",
        "derive_recoveries",
    },
}


class ModeAwareMetricCache:
    """Cache metric values and render irrelevant ones as ``'N/A'``.

    Each instance is initialised with the active mode. ``format`` returns the
    incoming ``value`` unless the metric is not relevant for the mode, in which
    case ``'N/A'`` is returned. Numeric metrics cache their last non-zero
    reading to avoid temporary zeros during sampling gaps.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._cache: Dict[str, float] = {}

    def format(self, metric: str, value) -> str | float:
        if metric not in MODE_METRICS.get(self.mode, set()):
            return "N/A"
        if isinstance(value, (int, float)):
            if value == 0:
                return self._cache.get(metric, 0.0)
            self._cache[metric] = float(value)
        return value


class CPUPercent:
    """Non-blocking process CPU% sampler.

    ``psutil.Process().cpu_percent`` with ``interval=None`` returns the delta
    since the previous call. Without storing state this leads to alternating
    zeros. This helper caches the last non-zero reading so callers always get a
    meaningful value.
    """

    def __init__(self) -> None:
        self._proc = psutil.Process()
        # Prime internal measurement to avoid an initial zero
        self._proc.cpu_percent(None)
        self._last = 0.0

    def sample(self) -> float:
        val = self._proc.cpu_percent(None)
        if val > 0.0:
            self._last = val
        return self._last
