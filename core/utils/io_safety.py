import os
import time
from typing import Tuple, TextIO


def atomic_open(path: str, mode: str = "w", encoding: str = "utf-8") -> Tuple[str, TextIO]:
    """Open a temp file in the same directory as ``path`` for atomic writes.

    Returns a tuple of (temp_path, handle). Caller should write to the handle
    and then invoke :func:`atomic_commit` to move it into place.
    """
    dir_name = os.path.dirname(path)
    base_name = os.path.basename(path)
    tmp_name = f".{base_name}.tmp-{os.getpid()}-{int(time.time()*1000)}"
    tmp_path = os.path.join(dir_name, tmp_name)
    handle = open(tmp_path, mode, encoding=encoding)
    return tmp_path, handle


def atomic_commit(tmp_path: str, final_path: str) -> None:
    """Flush ``tmp_path`` to disk and atomically replace ``final_path``.

    The temporary file should be closed before calling this function.
    """
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, final_path)


def safe_nonempty(path: str, min_bytes: int = 128) -> bool:
    """Return True if ``path`` exists and is at least ``min_bytes`` bytes."""
    try:
        return os.path.getsize(path) >= min_bytes
    except OSError:
        return False


def unique_path(base_path: str, suffix: str) -> str:
    """Return a unique filename based on ``base_path`` and ``suffix``."""
    candidate = f"{base_path}{suffix}"
    if not os.path.exists(candidate):
        return candidate
    i = 1
    while True:
        candidate = f"{base_path}{suffix}.{i}"
        if not os.path.exists(candidate):
            return candidate
        i += 1
