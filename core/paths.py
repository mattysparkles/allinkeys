"""Core path utilities using pathlib.Path.

This module centralizes filesystem paths and directory creation. It respects
ALLINKEYS_* environment variables while defaulting to config.settings values.
Always uses Path.mkdir(parents=True, exist_ok=True) for directories.
"""

from __future__ import annotations

import os
from pathlib import Path

from config import settings


def _env_path(var: str, default: Path) -> Path:
    v = os.getenv(var)
    return Path(v) if v else default


# Base directories
BASE_DIR: Path = Path(getattr(settings, "BASE_DIR", Path.cwd()))
LOG_DIR: Path = _env_path("ALLINKEYS_LOG_DIR", Path(getattr(settings, "LOG_DIR", BASE_DIR / "logs")))
DOWNLOADS_DIR: Path = _env_path(
    "ALLINKEYS_DOWNLOADS_DIR", Path(getattr(settings, "DOWNLOADS_DIR", BASE_DIR / "Downloads"))
)
OUTPUT_DIR: Path = _env_path("ALLINKEYS_OUTPUT_DIR", Path(getattr(settings, "OUTPUT_DIR", BASE_DIR / "output")))
CSV_DIR: Path = _env_path("ALLINKEYS_CSV_DIR", OUTPUT_DIR / "csv")
# Support both ``ALLINKEYS_VANITY_OUTPUT_DIR`` and the legacy
# ``ALLINKEYS_VANITY_TXT_DIR`` for vanity search outputs. Defaults now live
# under ``output/`` alongside CSVs.
VANITY_OUTPUT_DIR: Path = _env_path(
    "ALLINKEYS_VANITY_OUTPUT_DIR",
    _env_path("ALLINKEYS_VANITY_TXT_DIR", OUTPUT_DIR / "vanity_output"),
)
MNEMONIC_OUTPUT_DIR: Path = _env_path(
    "ALLINKEYS_MNEMONIC_TXT_DIR", OUTPUT_DIR / "mnemonic_output"
)
MATCH_LOG_DIR: Path = _env_path("ALLINKEYS_MATCH_LOG_DIR", Path(getattr(settings, "MATCH_LOG_DIR", LOG_DIR)))


def ensure_dirs() -> None:
    """Ensure common directories exist with parents allowed.

    This preserves the directory structure required by the application and CI
    while avoiding exceptions on re-creation.
    """
    for d in (LOG_DIR, DOWNLOADS_DIR, OUTPUT_DIR, CSV_DIR, VANITY_OUTPUT_DIR, MNEMONIC_OUTPUT_DIR, MATCH_LOG_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)

