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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seed_ranges (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            range_id   TEXT NOT NULL,
            start      INTEGER NOT NULL,
            end        INTEGER NOT NULL,
            first_seen REAL NOT NULL,
            last_seen  REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seed_ranges_range_start ON seed_ranges (range_id, start)"
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


def _seed_ranges_empty(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT 1 FROM seed_ranges LIMIT 1")
    return cur.fetchone() is None


def seed_in_used_range(seed: int | str, range_id: str = "default") -> bool:
    """Return ``True`` if ``seed`` was previously recorded for ``range_id``."""

    seed_value = int(seed)
    conn = _connect()
    try:
        def _op():
            if _seed_ranges_empty(conn):
                cur = conn.execute(
                    "SELECT 1 FROM used_seeds WHERE range_id=? AND seed_norm=? LIMIT 1",
                    (range_id, _norm(seed_value)),
                )
                return cur.fetchone() is not None
            cur = conn.execute(
                """
                SELECT 1
                FROM seed_ranges
                WHERE range_id=? AND start <= ? AND end >= ?
                LIMIT 1
                """,
                (range_id, seed_value, seed_value),
            )
            return cur.fetchone() is not None

        return _retry(_op)
    finally:
        conn.close()


def record_seed_range(first: int | str, last: int | str, range_id: str = "default") -> None:
    """Record the seed range ``[first, last]`` for ``range_id``."""

    start, end = int(first), int(last)
    if end < start:
        start, end = end, start

    conn = _connect()
    try:
        def _op():
            now = time.time()
            with conn:
                cur = conn.execute(
                    """
                    SELECT id, start, end, first_seen, last_seen
                    FROM seed_ranges
                    WHERE range_id=?
                      AND NOT (end < ? OR start > ?)
                    ORDER BY start
                    """,
                    (range_id, start - 1, end + 1),
                )
                rows = cur.fetchall()

                merged_start, merged_end = start, end
                first_seen = now
                last_seen = now

                if rows:
                    merged_start = min(merged_start, min(row[1] for row in rows))
                    merged_end = max(merged_end, max(row[2] for row in rows))
                    first_seen = min(first_seen, *(row[3] for row in rows))
                    last_seen = max(last_seen, *(row[4] for row in rows))
                    conn.executemany(
                        "DELETE FROM seed_ranges WHERE id=?",
                        [(row[0],) for row in rows],
                    )

                conn.execute(
                    """
                    INSERT INTO seed_ranges (range_id, start, end, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (range_id, merged_start, merged_end, first_seen, last_seen),
                )

        _retry(_op)
    finally:
        conn.close()


def _condense_seeds(seeds: List[int]) -> List[Tuple[int, int]]:
    if not seeds:
        return []
    ranges: List[Tuple[int, int]] = []
    start = prev = seeds[0]
    for seed in seeds[1:]:
        if seed == prev + 1:
            prev = seed
            continue
        ranges.append((start, prev))
        start = prev = seed
    ranges.append((start, prev))
    return ranges


def _migrate_used_seeds(conn: sqlite3.Connection, range_id: str) -> List[Tuple[int, int]]:
    cur = conn.execute(
        "SELECT seed_norm FROM used_seeds WHERE range_id=? ORDER BY seed_norm",
        (range_id,),
    )
    seeds = [int(row[0], 16) for row in cur.fetchall()]
    ranges = _condense_seeds(seeds)
    if not ranges:
        return []
    now = time.time()
    conn.executemany(
        """
        INSERT INTO seed_ranges (range_id, start, end, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(range_id, start, end, now, now) for start, end in ranges],
    )
    return ranges


def get_condensed_ranges(range_id: str = "default") -> List[Tuple[int, int]]:
    """Return consolidated seed ranges for ``range_id``."""

    conn = _connect()
    try:
        def _op():
            if _seed_ranges_empty(conn):
                return _migrate_used_seeds(conn, range_id)
            cur = conn.execute(
                "SELECT start, end FROM seed_ranges WHERE range_id=? ORDER BY start",
                (range_id,),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]

        return _retry(_op)
    finally:
        conn.close()
