import os

class RollingAtomicWriter:
    """Write lines to a temporary file then atomically rename on rotation.

    Parameters
    ----------
    path : str
        Destination file path when rotation occurs.
    max_lines : int
        Maximum number of lines to write before rotating.
    max_bytes : int
        Maximum number of bytes to write before rotating.
    """

    def __init__(self, path: str, max_lines: int, max_bytes: int) -> None:
        self.final_path = path
        self.temp_path = path + ".part"
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.lines = 0
        self.bytes = 0
        self._closed = False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(self.temp_path, "w", encoding="utf-8")

    def write(self, line: str) -> bool:
        """Write ``line`` to the file and return True if rotation occurred."""
        if self._closed:
            return True
        self._fh.write(line)
        self.lines += 1
        self.bytes += len(line.encode("utf-8"))
        if self.lines >= self.max_lines or self.bytes >= self.max_bytes:
            self.rotate()
            return True
        return False

    def rotate(self) -> None:
        if self._closed:
            return
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        os.replace(self.temp_path, self.final_path)
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        try:
            self._fh.close()
        finally:
            if os.path.exists(self.temp_path):
                os.remove(self.temp_path)
        self._closed = True

    def close(self) -> None:
        if not self._closed:
            self.rotate()
