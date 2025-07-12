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
