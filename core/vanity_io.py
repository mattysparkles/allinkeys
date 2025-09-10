import os
import time
from typing import Optional, BinaryIO, Union
from pathlib import Path


def ensure_dir(path: Union[str, Path]) -> str:
    """Create ``path`` if missing and return it as a string.

    Historically ``ensure_dir`` returned the same object that was passed in,
    which could be a :class:`Path`.  Callers such as :class:`RollingAtomicWriter`
    and ``tempfile`` always expect a string representation, so returning the
    original ``Path`` could lead to subtle inconsistencies on older Python
    versions or when concatenating paths.  Normalising to ``str`` keeps the
    return type predictable while still accepting ``Path`` instances.
    """

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


class RollingAtomicWriter:
    """Write lines to rolling files using atomic commit on rotation."""

    def __init__(
        self,
        directory: str,
        rotate_lines: int,
        max_bytes: int,
        prefix: str = "vanity",
        rotate_seconds: Optional[int] = None,
    ) -> None:
        """Create a new rolling writer.

        Parameters
        ----------
        directory:
            Destination directory for output files.
        rotate_lines:
            Rotate once this many lines have been written.
        max_bytes:
            Rotate once file reaches this size in bytes.
        prefix:
            Filename prefix for generated files.
        rotate_seconds:
            If provided, rotate the file after this many seconds
            regardless of line or byte count.
        """

        self.directory = ensure_dir(directory)
        self.rotate_lines = rotate_lines
        self.max_bytes = max_bytes
        self.prefix = prefix
        self.rotate_seconds = rotate_seconds
        self._counter = 0
        # Open files in binary mode so byte counting matches on all platforms
        self._fh: Optional[BinaryIO] = None
        self._lines = 0
        self._bytes = 0
        self._open_new_file()

    # Internal helpers -------------------------------------------------
    def _next_filename(self) -> str:
        self._counter += 1
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"{self.prefix}_{ts}_{self._counter:03d}.txt"
        return str((Path(self.directory) / name).resolve())

    def _open_new_file(self) -> None:
        self.final_path = self._next_filename()
        self.temp_path = self.final_path + ".part"
        # Binary mode avoids newline translation which can throw off size
        # calculations, especially on Windows.
        self._fh = open(self.temp_path, "wb")
        self._lines = 0
        self._bytes = 0
        self._opened = time.time()

    def _commit(self) -> None:
        if not self._fh:
            return
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        Path(self.temp_path).replace(self.final_path)
        self._fh = None

    # Public API -------------------------------------------------------
    def write(self, text: str) -> bool:
        """Legacy write that accepts a full line (with newline)."""
        if not self._fh:
            return False
        data = text.encode("utf-8")
        self._fh.write(data)
        self._lines += 1
        self._bytes += len(data)
        rotated = (
            self._lines >= self.rotate_lines
            or self._bytes >= self.max_bytes
            or (
                self.rotate_seconds is not None
                and (time.time() - self._opened) >= self.rotate_seconds
            )
        )
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
                p = Path(self.temp_path)
                if p.exists():
                    p.unlink(missing_ok=True)
            self._fh = None
