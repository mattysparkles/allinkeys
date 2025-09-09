import multiprocessing
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.pop("core.gpu_scheduler", None)
gpu_scheduler = importlib.import_module("core.gpu_scheduler")


def test_detect_gpu_vendor_respects_setting(monkeypatch):
    monkeypatch.setattr(gpu_scheduler, "GPU_VENDOR", "nvidia")
    assert gpu_scheduler._detect_gpu_vendor() == ("nvidia", "nvidia")


def run_scheduler_once(backlog, monkeypatch):
    shared_metrics = {}
    vanity_flag = multiprocessing.Value("i", 1)
    altcoin_flag = multiprocessing.Value("i", 0)
    assignment_flag = multiprocessing.Value("i", 0)
    shutdown_event = multiprocessing.Event()

    monkeypatch.setattr(gpu_scheduler, "SWING_MODE", True)
    monkeypatch.setattr(gpu_scheduler, "_detect_gpu_vendor", lambda: (None, None))
    monkeypatch.setattr("core.worker_bootstrap.ensure_metrics_ready", lambda m: None)

    metrics = {}
    monkeypatch.setattr("core.worker_bootstrap._safe_set_metric", lambda k, v: metrics.update({k: v}))
    monkeypatch.setattr(gpu_scheduler.os, "listdir", lambda p: [f"f{i}.txt" for i in range(backlog)])
    class ImmediateTimer:
        def __init__(self, interval, func):
            self.func = func

        def start(self):
            shutdown_event.set()

    monkeypatch.setattr(gpu_scheduler.threading, "Timer", lambda i, f: ImmediateTimer(i, f))

    gpu_scheduler.monitor_backlog_and_reassign(
        shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event
    )
    return vanity_flag.value, altcoin_flag.value, assignment_flag.value, metrics.get("gpu_assignment")


def test_monitor_backlog_high(monkeypatch):
    v, a, assign, assignment = run_scheduler_once(120, monkeypatch)
    assert (v, a, assign) == (0, 1, 1)
    assert assignment == "altcoin"


def test_monitor_backlog_low(monkeypatch):
    v, a, assign, assignment = run_scheduler_once(10, monkeypatch)
    assert (v, a, assign) == (1, 0, 0)
    assert assignment == "vanity"
