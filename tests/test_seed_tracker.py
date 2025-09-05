import json
from core import seed_tracker


def test_seed_tracker_roundtrip(tmp_path, monkeypatch):
    log_path = tmp_path / "ranges.json"
    monkeypatch.setattr(seed_tracker, "SEED_RANGES_PATH", str(log_path))
    assert seed_tracker.seed_in_used_range(10) is False
    seed_tracker.record_seed_range(5, 15)
    assert seed_tracker.seed_in_used_range(10) is True
    assert seed_tracker.seed_in_used_range(16) is False
    # ensure data persisted
    with open(log_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{"start": 5, "end": 15}]
