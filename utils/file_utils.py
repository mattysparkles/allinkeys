import os
from glob import glob

from config.settings import DOWNLOADS_DIR, FULL_DIR, UNIQUE_DIR


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


def secure_delete(path: str, passes: int = 1) -> bool:
    """Overwrite a file with random bytes before removing it.

    Parameters
    ----------
    path : str
        Path to the file to securely delete.
    passes : int, optional
        Number of overwrite passes. Defaults to 1.

    Returns
    -------
    bool
        ``True`` on success, ``False`` otherwise.
    """

    try:
        if not os.path.isfile(path):
            return False
        length = os.path.getsize(path)
        with open(path, "ba+", buffering=0) as f:
            for _ in range(passes):
                f.seek(0)
                f.write(os.urandom(length))
                f.flush()
                os.fsync(f.fileno())
        os.remove(path)
        return True
    except Exception:
        return False
