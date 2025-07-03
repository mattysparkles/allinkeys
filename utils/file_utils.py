import os
from glob import glob

from config.settings import DOWNLOADS_DIR, FULL_DIR, UNIQUE_DIR


def find_latest_funded_file(coin: str, directory: str = DOWNLOADS_DIR) -> str | None:
    """Return the newest funded address file for ``coin`` within ``directory``.

    Files are expected to follow the pattern ``{COIN}_addresses_*.txt`` where
    ``COIN`` is the uppercase coin symbol. ``directory`` defaults to
    :data:`config.settings.DOWNLOADS_DIR` but can be overridden when searching
    subfolders like ``FULL_DIR`` or ``UNIQUE_DIR``.
    """
    pattern = os.path.join(directory, f"{coin.upper()}_addresses_*.txt")
    files = glob(pattern)
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    return latest
