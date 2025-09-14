import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dashboard.metrics_window import KPSWindow, ModeAwareMetricCache


def test_kps_window_rolling():
    win = KPSWindow(window_seconds=5, use_ewma=False)
    win.update(0, timestamp=0)
    win.update(50, timestamp=5)
    rate = win.update(100, timestamp=10)
    assert math.isclose(rate, 10.0)


def test_kps_window_ewma():
    win = KPSWindow(window_seconds=10, use_ewma=True, alpha=0.3)
    win.update(0, timestamp=0)
    win.update(10, timestamp=1)  # raw rate 10 -> ewma 3
    rate = win.update(30, timestamp=2)  # raw rate 15 -> ewma 6.6
    assert math.isclose(rate, 6.6, rel_tol=1e-6)


def test_mode_cache_irrelevant_metric():
    cache = ModeAwareMetricCache("mnemonic")
    assert cache.format("derive_kps", 123) == "N/A"
