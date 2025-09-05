import json
import os
from typing import List, Tuple

from config.settings import LOG_DIR

SEED_RANGES_PATH = os.path.join(LOG_DIR, "used_seeds.json")


def _load_ranges() -> List[Tuple[int, int]]:
    if not os.path.exists(SEED_RANGES_PATH):
        return []
    try:
        with open(SEED_RANGES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ranges = []
        for item in data:
            start = int(item.get("start", 0))
            end = int(item.get("end", 0))
            ranges.append((start, end))
        return ranges
    except Exception:
        return []


def _save_ranges(ranges: List[Tuple[int, int]]) -> None:
    os.makedirs(os.path.dirname(SEED_RANGES_PATH), exist_ok=True)
    data = [{"start": s, "end": e} for s, e in ranges]
    with open(SEED_RANGES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def record_seed_range(start: int, end: int) -> None:
    """Record a processed seed range to the tracking file."""
    ranges = _load_ranges()
    ranges.append((int(start), int(end)))
    _save_ranges(ranges)


def seed_in_used_range(seed: int) -> bool:
    """Return ``True`` if ``seed`` falls within a previously used range."""
    seed = int(seed)
    for start, end in _load_ranges():
        if start <= seed <= end:
            return True
    return False
