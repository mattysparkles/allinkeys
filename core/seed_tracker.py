import json
import os
from bisect import bisect_left
from typing import List, Tuple
from pathlib import Path

from config.settings import LOG_DIR
from core.paths import LOG_DIR as LOG_DIR_P, ensure_dirs

SEED_RANGES_PATH = str((Path(LOG_DIR_P) / "used_seeds.json").resolve())


def _load_ranges() -> List[Tuple[int, int]]:
    """Load raw ranges from disk without modification."""
    if not Path(SEED_RANGES_PATH).exists():
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
    Path(SEED_RANGES_PATH).parent.mkdir(parents=True, exist_ok=True)
    data = [{"start": s, "end": e} for s, e in ranges]
    with open(SEED_RANGES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _condense_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Return sorted, non-overlapping ranges merged where necessary."""
    if not ranges:
        return []
    ranges = sorted((int(s), int(e)) for s, e in ranges)
    merged = [ranges[0]]
    for cur_start, cur_end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if cur_start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, cur_end))
        else:
            merged.append((cur_start, cur_end))
    return merged


def condense_seed_log() -> None:
    """Load, merge and persist the used seed ranges to keep them minimal."""
    ranges = _condense_ranges(_load_ranges())
    _save_ranges(ranges)


def get_condensed_ranges() -> List[Tuple[int, int]]:
    """Return the current used seed ranges in condensed form."""
    return _condense_ranges(_load_ranges())


def record_seed_range(start: int, end: int) -> None:
    """Record a processed seed range, merging with existing ranges."""
    ranges = _load_ranges()
    ranges.append((int(start), int(end)))
    _save_ranges(_condense_ranges(ranges))


def seed_in_used_range(seed: int, ranges: List[Tuple[int, int]] | None = None) -> bool:
    """Return ``True`` if ``seed`` falls within a previously used range."""
    seed = int(seed)
    if ranges is None:
        ranges = _condense_ranges(_load_ranges())
    starts = [s for s, _ in ranges]
    idx = bisect_left(starts, seed)
    if idx < len(ranges) and ranges[idx][0] <= seed <= ranges[idx][1]:
        return True
    if idx > 0 and ranges[idx - 1][0] <= seed <= ranges[idx - 1][1]:
        return True
    return False
