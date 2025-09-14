import os
import sys
import time
import multiprocessing

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.altcoin_derive import _monitor_watchdogs


def slow_writer(path):
    with open(path, "w") as f:
        f.write("row1\n")
        f.flush()
        time.sleep(5)


def test_watchdog_restarts_on_stall(monkeypatch, tmp_path):
    metrics = {}
    monkeypatch.setattr(
        "core.altcoin_derive.safe_increment_metric",
        lambda key, amount=1: metrics.__setitem__(key, metrics.get(key, 0) + amount),
    )
    monkeypatch.setattr("core.altcoin_derive.safe_update_dashboard_stat", lambda *a, **k: None)
    monkeypatch.setattr("core.altcoin_derive.DERIVE_STALL_SECONDS", 1)

    partial = tmp_path / "test_part_0.partial.csv"
    txt_name = "dummy.txt"

    proc = multiprocessing.Process(target=slow_writer, args=(partial,))
    proc.start()

    watchdogs = {
        None: {
            "path": str(partial),
            "last_size": 0,
            "last_lines": 0,
            "last_time": time.time(),
            "start": time.time(),
            "saw_growth": False,
            "txt": txt_name,
        }
    }
    processes = {None: proc}
    gpu_queues = {None: []}
    backoff = {None: 1}

    time.sleep(0.2)

    for _ in range(5):
        _monitor_watchdogs(watchdogs, processes, gpu_queues, backoff)
        if not processes.get(None):
            break
        time.sleep(0.3)

    proc.join(timeout=1)

    assert not processes.get(None)
    assert gpu_queues[None] == [txt_name]
    assert metrics.get("derive_recoveries") == 1
    final = str(partial).replace(".partial", "")
    assert os.path.exists(final)
    with open(final) as f:
        lines = f.readlines()
    assert lines == ["row1\n"]

