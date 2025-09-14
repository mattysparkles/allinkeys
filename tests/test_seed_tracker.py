import threading
import sqlite3

from core import seed_tracker


def test_seed_tracker_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "used_seeds.db"
    monkeypatch.setattr(seed_tracker, "SEED_DB_PATH", str(db_path))

    assert seed_tracker.seed_in_used_range(10) is False
    seed_tracker.record_seed_range(5, 15)
    seed_tracker.record_seed_range(20, 25)
    seed_tracker.record_seed_range(15, 20)

    ranges = seed_tracker.get_condensed_ranges()
    assert ranges == [(5, 25)]
    assert seed_tracker.seed_in_used_range(10) is True
    assert seed_tracker.seed_in_used_range(30) is False

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM used_seeds").fetchone()[0]
    finally:
        conn.close()
    assert rows == 21  # seeds 5..25 inclusive


def test_seed_tracker_concurrency(tmp_path, monkeypatch):
    db_path = tmp_path / "used_seeds.db"
    monkeypatch.setattr(seed_tracker, "SEED_DB_PATH", str(db_path))

    def worker():
        seed_tracker.record_seed_range(1, 5)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ranges = seed_tracker.get_condensed_ranges()
    assert ranges == [(1, 5)]
