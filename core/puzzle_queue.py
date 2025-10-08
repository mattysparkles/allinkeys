"""
Generic chunk manager for Bitcoin puzzle ranges.
Claims non-overlapping key ranges and records per-worker progress.
"""
import os
import sqlite3
import json
import time
from typing import Optional, Tuple
from pathlib import Path

from core.paths import LOG_DIR as LOG_DIR_P
from core.logger import get_logger
from utils.puzzle import get_puzzle_info

try:  # pragma: no cover - optional in minimal environments
    from core.dashboard import increment_metric
except Exception:  # pragma: no cover
    def increment_metric(*args, **kwargs):
        """Fallback metric increment when dashboard is unavailable."""
        return None

logger = get_logger(__name__)

DB_PATH = str((Path(LOG_DIR_P) / "work_queue.db").resolve())
CHUNK_SIZE = 1 << 20  # ~1M keys


def _checkpoint_file(puzzle: int) -> str:
    return str((Path(LOG_DIR_P) / f"puzzle{puzzle}_checkpoint.json").resolve())


def init_work_queue() -> None:
    """Ensure the work_queue table exists."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS work_queue(
            puzzle      INTEGER,
            chunk_start TEXT,
            chunk_end   TEXT,
            status      TEXT,
            assignee    TEXT,
            updated_at  REAL,
            PRIMARY KEY (puzzle, chunk_start)
        )"""
        )
        conn.commit()
    finally:
        conn.close()


def _get_bounds(puzzle: int) -> Tuple[int, int]:
    info = get_puzzle_info(puzzle)
    start = int(info["start"], 16)
    end = int(info["end"], 16) + 1  # make half-open
    return start, end


def claim_next_chunk(puzzle: int, assignee: str) -> Optional[Tuple[int, int]]:
    """Atomically claim the next unprocessed chunk for a puzzle."""
    start_bound, end_bound = _get_bounds(puzzle)
    width = len(str(end_bound))
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.isolation_level = "EXCLUSIVE"
        cur = conn.cursor()
        cur.execute("BEGIN EXCLUSIVE")
        cur.execute(
            """SELECT chunk_start, chunk_end
                       FROM work_queue
                       WHERE puzzle=? AND status='pending'
                       ORDER BY chunk_start LIMIT 1""",
            (puzzle,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT chunk_end FROM work_queue WHERE puzzle=? ORDER BY chunk_end DESC LIMIT 1",
                (puzzle,),
            )
            last = cur.fetchone()
            last_end = int(last[0]) if last else None
            next_start = start_bound if last_end is None else last_end
            if next_start >= end_bound:
                return None
            end = min(next_start + CHUNK_SIZE, end_bound)
            cur.execute(
                "INSERT INTO work_queue VALUES (?,?,?,?,?,?)",
                (
                    puzzle,
                    str(next_start).zfill(width),
                    str(end).zfill(width),
                    "pending",
                    None,
                    time.time(),
                ),
            )
            row = (str(next_start).zfill(width), str(end).zfill(width))
        cur.execute(
            """UPDATE work_queue
                       SET status='claimed', assignee=?, updated_at=?
                       WHERE puzzle=? AND chunk_start=?""",
            (assignee, time.time(), puzzle, row[0]),
        )
        conn.commit()
        return int(row[0]), int(row[1])
    finally:
        conn.close()


def claim_chunk(puzzle: int, chunk_index: int, assignee: str) -> Optional[Tuple[int, int]]:
    """Claim a specific chunk by index.

    ``chunk_index`` is zero-based within the puzzle's keyspace.
    Returns ``(start, end)`` of the chunk or ``None`` if unavailable.
    """
    start_bound, end_bound = _get_bounds(puzzle)
    chunk_start = start_bound + chunk_index * CHUNK_SIZE
    if chunk_start >= end_bound:
        return None
    chunk_end = min(chunk_start + CHUNK_SIZE, end_bound)
    width = len(str(end_bound))
    key = str(chunk_start).zfill(width)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.isolation_level = "EXCLUSIVE"
        cur = conn.cursor()
        cur.execute("BEGIN EXCLUSIVE")
        cur.execute(
            "SELECT status FROM work_queue WHERE puzzle=? AND chunk_start=?",
            (puzzle, key),
        )
        row = cur.fetchone()
        if row:
            if row[0] != "pending":
                return None
            cur.execute(
                """UPDATE work_queue
                           SET status='claimed', assignee=?, updated_at=?
                           WHERE puzzle=? AND chunk_start=?""",
                (assignee, time.time(), puzzle, key),
            )
        else:
            cur.execute(
                "INSERT INTO work_queue VALUES (?,?,?,?,?,?)",
                (
                    puzzle,
                    key,
                    str(chunk_end).zfill(width),
                    "claimed",
                    assignee,
                    time.time(),
                ),
            )
        conn.commit()
        return chunk_start, chunk_end
    finally:
        conn.close()

def load_checkpoint(puzzle: int) -> dict:
    """Load worker progress within the current chunk for a puzzle."""
    path = _checkpoint_file(puzzle)
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_checkpoint(puzzle: int, state: dict) -> None:
    """Persist worker progress for crash recovery."""
    state["updated_at"] = time.time()
    path = _checkpoint_file(puzzle)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=4)


def next_seed(puzzle: int, assignee: str, chunk_index: Optional[int] = None) -> Optional[int]:
    """Return the next seed to test for a puzzle.

    If ``chunk_index`` is provided, the worker will claim that specific chunk
    before generating seeds.
    """
    state = load_checkpoint(puzzle)
    start = state.get("chunk_start")
    end = state.get("chunk_end")
    cur = state.get("cursor")

    if chunk_index is not None:
        desired_start = _get_bounds(puzzle)[0] + chunk_index * CHUNK_SIZE
        if start != desired_start or cur is None or cur >= end:
            claim = claim_chunk(puzzle, chunk_index, assignee)
            if not claim:
                return None
            start, end = claim
            cur = start
    elif start is None or cur >= end:
        claim = claim_next_chunk(puzzle, assignee)
        if not claim:
            return None  # no more work
        start, end = claim
        cur = start

    start_bound, end_bound = _get_bounds(puzzle)
    if not (start_bound <= start < end <= end_bound):
        logger.debug(
            "Chunk [%x, %x) outside puzzle range [%x, %x) — skipping",
            start,
            end,
            start_bound,
            end_bound,
        )
        increment_metric("out_of_range_skipped", 1)
        return None

    seed = cur
    save_checkpoint(puzzle, {"chunk_start": start, "chunk_end": end, "cursor": seed + 1})
    return seed
