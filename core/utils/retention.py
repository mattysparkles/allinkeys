"""Retention utilities for cleaning old output files.

Implements the --purge [days] feature with --dry-run support, honoring
exclusions (.log, checkpoints, active .txt) and preserving directory layout.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Tuple, List

from core.logger import log_message
from core.paths import CSV_DIR, VANITY_OUTPUT_DIR


EXCLUDE_EXTENSIONS = {".log"}
EXCLUDE_NAMES = {"checkpoints", "checkpoint", "active.txt"}


def _should_skip(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return True
    if any(tag in name for tag in EXCLUDE_NAMES):
        return True
    return False


def _iter_targets() -> Iterable[Path]:
    for root in (VANITY_OUTPUT_DIR, CSV_DIR):
        if not Path(root).exists():
            continue
        for p in Path(root).rglob("*"):
            if p.is_file() and not _should_skip(p):
                yield p


def purge_older_than(days: int, *, dry_run: bool = False) -> Tuple[int, List[str]]:
    """Delete files older than N days in vanity_output/ and output/csv/.

    Returns a tuple (count, messages) describing actions performed or planned.
    """
    now = time.time()
    cutoff = now - days * 86400
    removed = 0
    messages: List[str] = []
    for file in _iter_targets():
        try:
            mtime = file.stat().st_mtime
            if mtime < cutoff:
                rel = str(file)
                if dry_run:
                    messages.append(f"DRY-RUN: would delete {rel}")
                else:
                    file.unlink(missing_ok=True)
                    messages.append(f"Deleted {rel}")
                    removed += 1
        except Exception as e:
            log_message(f"⚠️ purge skipped {file}: {e}", "WARN")
    log_message(f"🧹 Purge complete. {'Planned' if dry_run else 'Removed'} {removed} files.")
    return removed, messages

