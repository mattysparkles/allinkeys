from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import json
from pathlib import Path
import threading
from typing import Any, Iterable, Optional

from config.directories import LOG_DIR


QUEUE_PATH = Path(LOG_DIR) / "seed_queue.json"
MAX_QUEUE_SIZE = 100

_QUEUE_LOCK = threading.Lock()
_QUEUE_LOADED = False
_QUEUE: deque["SeedQueueEntry"] = deque()


@dataclass
class SeedQueueEntry:
    seed_start: int
    seed_end: Optional[int] = None
    range_id: Optional[str] = None
    range_value: Optional[str] = None
    position_percent: Optional[float] = None


def _parse_seed_value(value: str) -> int:
    cleaned = value.strip().lower().replace("_", "").replace(",", "")
    if not cleaned:
        raise ValueError("Empty seed value")
    if cleaned.startswith("0x"):
        return int(cleaned, 16)
    return int(cleaned, 10)


def _entry_to_dict(entry: SeedQueueEntry) -> dict[str, Any]:
    return {
        "seed_start": entry.seed_start,
        "seed_end": entry.seed_end,
        "range_id": entry.range_id,
        "range_value": entry.range_value,
        "position_percent": entry.position_percent,
    }


def _entry_from_dict(payload: dict[str, Any]) -> Optional[SeedQueueEntry]:
    if not isinstance(payload, dict):
        return None
    seed_start = payload.get("seed_start")
    if seed_start is None:
        return None
    try:
        seed_start_val = (
            _parse_seed_value(seed_start)
            if isinstance(seed_start, str)
            else int(seed_start)
        )
    except (TypeError, ValueError):
        return None
    seed_end_val = None
    seed_end = payload.get("seed_end")
    if seed_end is not None:
        try:
            seed_end_val = (
                _parse_seed_value(seed_end)
                if isinstance(seed_end, str)
                else int(seed_end)
            )
        except (TypeError, ValueError):
            seed_end_val = None
    return SeedQueueEntry(
        seed_start=seed_start_val,
        seed_end=seed_end_val,
        range_id=payload.get("range_id"),
        range_value=payload.get("range_value"),
        position_percent=payload.get("position_percent"),
    )


def _load_queue() -> None:
    global _QUEUE_LOADED
    if _QUEUE_LOADED:
        return
    with _QUEUE_LOCK:
        if _QUEUE_LOADED:
            return
        _QUEUE.clear()
        try:
            if QUEUE_PATH.exists():
                payload = json.loads(QUEUE_PATH.read_text())
                if isinstance(payload, list):
                    for entry in payload:
                        parsed = _entry_from_dict(entry)
                        if parsed is not None:
                            _QUEUE.append(parsed)
        except Exception:
            _QUEUE.clear()
        if len(_QUEUE) > MAX_QUEUE_SIZE:
            while len(_QUEUE) > MAX_QUEUE_SIZE:
                _QUEUE.popleft()
        _QUEUE_LOADED = True


def _persist_queue() -> None:
    try:
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = [_entry_to_dict(entry) for entry in list(_QUEUE)]
        QUEUE_PATH.write_text(json.dumps(payload, indent=2))
    except Exception:
        return


def size() -> int:
    _load_queue()
    with _QUEUE_LOCK:
        return len(_QUEUE)


def list_entries() -> list[SeedQueueEntry]:
    _load_queue()
    with _QUEUE_LOCK:
        return list(_QUEUE)


def clear() -> None:
    _load_queue()
    with _QUEUE_LOCK:
        _QUEUE.clear()
        _persist_queue()


def enqueue(entry: SeedQueueEntry) -> bool:
    _load_queue()
    with _QUEUE_LOCK:
        if len(_QUEUE) >= MAX_QUEUE_SIZE:
            return False
        _QUEUE.append(entry)
        _persist_queue()
        return True


def dequeue() -> Optional[SeedQueueEntry]:
    _load_queue()
    with _QUEUE_LOCK:
        if not _QUEUE:
            return None
        entry = _QUEUE.popleft()
        _persist_queue()
        return entry


def _parse_range_text(value: str) -> Optional[SeedQueueEntry]:
    cleaned = value.strip()
    if not cleaned:
        return None
    if "-" in cleaned:
        start_text, end_text = cleaned.split("-", 1)
        start = _parse_seed_value(start_text)
        end = _parse_seed_value(end_text)
        if end < start:
            start, end = end, start
        return SeedQueueEntry(seed_start=start, seed_end=end, range_value=cleaned)
    start = _parse_seed_value(cleaned)
    return SeedQueueEntry(seed_start=start, range_value=cleaned)


def parse_queue_value(value: Any) -> list[SeedQueueEntry]:
    if value is None:
        return []
    if isinstance(value, SeedQueueEntry):
        return [value]
    if isinstance(value, dict):
        entry = _entry_from_dict(value)
        return [entry] if entry else []
    if isinstance(value, list):
        entries: list[SeedQueueEntry] = []
        for item in value:
            entries.extend(parse_queue_value(item))
        return entries
    if isinstance(value, (int, float)):
        return [SeedQueueEntry(seed_start=int(value))]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if parsed is not None:
                return parse_queue_value(parsed)
        entry = _parse_range_text(text)
        return [entry] if entry else []
    return []


def enqueue_many(entries: Iterable[SeedQueueEntry]) -> int:
    added = 0
    for entry in entries:
        if enqueue(entry):
            added += 1
        else:
            break
    return added
