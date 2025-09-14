"""Track used seed ranges with a SQLite backend.

This module maintains a lightweight database of processed seeds.  It replaces
the previous JSON based tracker with a more robust solution that supports
concurrent writers and provides quick membership checks.  Seeds are stored in a
normalized hexadecimal form along with a ``range_id`` to distinguish different
key spaces (e.g. standard search vs. puzzle mode).

Public API:

``seed_in_used_range(seed, range_id="default")``
    Return ``True`` if ``seed`` has been recorded for ``range_id``.

``record_seed_range(first, last, range_id="default")``
    Persist every seed in ``[first, last]`` for ``range_id``.

``get_condensed_ranges(range_id="default")``
    Return a list of consolidated ``(start, end)`` pairs for ``range_id``.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

from core.paths import LOG_DIR as LOG_DIR_P, ensure_dirs

# SQLite database path and table definition
SEED_DB_PATH = str((Path(LOG_DIR_P) / "used_seeds.db").resolve())


def _norm(seed: int | str) -> str:
    """Return a 64-char lowercase hex representation for ``seed``."""
    return f"{int(seed):064x}"


def _connect() -> sqlite3.Connection:
    """Return a SQLite connection in WAL mode."""
    ensure_dirs()
    conn = sqlite3.connect(SEED_DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS used_seeds (
            range_id  TEXT NOT NULL,
            seed_norm TEXT NOT NULL,
            PRIMARY KEY (seed_norm, range_id)
        )
        """
    )
    return conn


def _retry(op, *args, **kwargs):
    """Execute ``op`` with retries when the database is locked."""
    for _ in range(5):
        try:
            return op(*args, **kwargs)
        except sqlite3.OperationalError as exc:  # pragma: no cover - rare
            if "locked" in str(exc).lower():
                time.sleep(0.1)
                continue
            raise
    raise RuntimeError("database is locked")


def seed_in_used_range(seed: int | str, range_id: str = "default") -> bool:
    """Return ``True`` if ``seed`` was previously recorded for ``range_id``."""

    conn = _connect()
    try:
        def _op():
            cur = conn.execute(
                "SELECT 1 FROM used_seeds WHERE range_id=? AND seed_norm=? LIMIT 1",
                (range_id, _norm(seed)),
            )
            return cur.fetchone() is not None

        return _retry(_op)
    finally:
        conn.close()


def record_seed_range(first: int | str, last: int | str, range_id: str = "default") -> None:
    """Record every seed in ``[first, last]`` for ``range_id``.

    Duplicate inserts are ignored thanks to the primary key constraint.
    """

    start, end = int(first), int(last)
    if end < start:
        start, end = end, start
    seeds = [(range_id, _norm(s)) for s in range(start, end + 1)]

    conn = _connect()
    try:
        def _op():
            with conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO used_seeds (range_id, seed_norm) VALUES (?, ?)",
                    seeds,
                )

        _retry(_op)
    finally:
        conn.close()


def get_condensed_ranges(range_id: str = "default") -> List[Tuple[int, int]]:
    """Return consolidated seed ranges for ``range_id``."""

    conn = _connect()
    try:
        def _op():
            cur = conn.execute(
                "SELECT seed_norm FROM used_seeds WHERE range_id=? ORDER BY seed_norm",
                (range_id,),
            )
            return [int(r[0], 16) for r in cur.fetchall()]

        seeds = _retry(_op)
    finally:
        conn.close()

    if not seeds:
        return []

    ranges: List[Tuple[int, int]] = []
    start = prev = seeds[0]
    for s in seeds[1:]:
        if s == prev + 1:
            prev = s
            continue
        ranges.append((start, prev))
        start = prev = s
    ranges.append((start, prev))
    return ranges

