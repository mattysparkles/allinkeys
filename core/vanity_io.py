import os
import time
from typing import Optional, TextIO


def ensure_dir(path: str) -> str:
    """Create ``path`` if missing and return it."""
    os.makedirs(path, exist_ok=True)
    return path


class RollingAtomicWriter:
    """Write lines to rolling files using atomic commit on rotation."""

    def __init__(
        self,
        directory: str,
        rotate_lines: int,
        max_bytes: int,
        prefix: str = "vanity",
    ) -> None:
        self.directory = ensure_dir(directory)
        self.rotate_lines = rotate_lines
        self.max_bytes = max_bytes
        self.prefix = prefix
        self._counter = 0
        self._fh: Optional[TextIO] = None
        self._lines = 0
        self._bytes = 0
        self._open_new_file()

    # Internal helpers -------------------------------------------------
    def _next_filename(self) -> str:
        self._counter += 1
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"{self.prefix}_{ts}_{self._counter:03d}.txt"
        return os.path.join(self.directory, name)

    def _open_new_file(self) -> None:
        self.final_path = self._next_filename()
        self.temp_path = self.final_path + ".part"
        self._fh = open(self.temp_path, "w", encoding="utf-8")
        self._lines = 0
        self._bytes = 0

    def _commit(self) -> None:
        if not self._fh:
            return
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        os.replace(self.temp_path, self.final_path)
        self._fh = None

    # Public API -------------------------------------------------------
    def write(self, text: str) -> bool:
        """Legacy write that accepts a full line (with newline)."""
        if not self._fh:
            return False
        self._fh.write(text)
        self._lines += 1
        self._bytes += len(text.encode("utf-8"))
        rotated = self._lines >= self.rotate_lines or self._bytes >= self.max_bytes
        if rotated:
            self._commit()
            self._open_new_file()
        return rotated

    def write_line(self, line: str) -> None:
        """Write a single line (newline appended)."""
        self.write(line + "\n")

    def close(self) -> None:
        """Finalize the current file if open."""
        if self._fh:
            self._commit()

    def abort(self) -> None:
        """Abort the current file and remove the temp file."""
        if self._fh:
            try:
                self._fh.close()
            finally:
                if os.path.exists(self.temp_path):
                    os.remove(self.temp_path)
            self._fh = None

