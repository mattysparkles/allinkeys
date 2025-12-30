import os
import time
import threading
from glob import glob

from typing import Optional

from config.directories import DOWNLOADS_DIR
from config.settings import RETENTION_DAYS
from core.logger import log_message
from utils.thread_guard import can_spawn_thread


def find_latest_funded_file(
    coin: str,
    directory: str = DOWNLOADS_DIR,
    *,
    unique: bool = False,
) -> str | None:
    """Return the newest funded address list for ``coin``.

    Parameters
    ----------
    coin : str
        The coin symbol to search for (e.g. ``btc``).
    directory : str, optional
        Directory to search. Defaults to :data:`DOWNLOADS_DIR`.
    unique : bool, optional
        If ``True``, search for ``*_UNIQUE_addresses_*`` files instead of the
        full ``*_addresses_*`` lists.
    """

    suffix = "_UNIQUE_addresses_" if unique else "_addresses_"
    pattern = os.path.join(directory, f"{coin.upper()}{suffix}*.txt")
    files = glob(pattern)
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    return latest


def cleanup_old_files(
    directory: str = DOWNLOADS_DIR,
    retention_days: int = RETENTION_DAYS,
) -> int:
    """Remove files older than ``retention_days`` from ``directory``.

    Returns
    -------
    int
        Number of files removed.
    """

    if not os.path.isdir(directory):
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
    if removed:
        log_message(
            f"\U0001F9F9 Purged {removed} file(s) older than {retention_days} days from {directory}"
        )
    return removed


def start_daily_cleanup(
    directory: str = DOWNLOADS_DIR,
    retention_days: int = RETENTION_DAYS,
) -> Optional[threading.Thread]:
    """Start a daemon thread that runs cleanup once per day."""

    def _loop() -> None:
        while True:
            cleanup_old_files(directory, retention_days)
            time.sleep(24 * 60 * 60)

    if not can_spawn_thread("daily_cleanup"):
        log_message("⚠️ Daily cleanup thread skipped; thread limit reached", "WARN")
        return None

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
