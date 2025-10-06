"""Environment helpers for configuration modules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load variables from a local .env file once on import so all configuration
# modules share the same environment state.
load_dotenv()

_FALSE_VALUES = {"0", "false", "False", "no", "No"}


def env_flag(var: str, default: bool = False) -> bool:
    """Return ``True`` unless ``var`` is an explicit falsey string."""

    raw = os.getenv(var)
    if raw is None:
        return default
    return raw not in _FALSE_VALUES


def env_int(var: str, default: Optional[int]) -> Optional[int]:
    """Return ``int`` value for ``var`` falling back to ``default``."""

    raw = os.getenv(var)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_path(var: str, default: Path | str) -> Path:
    """Return :class:`Path` resolved from ``var`` or ``default``."""

    value = os.getenv(var)
    return Path(value) if value else Path(default)


__all__ = ["env_flag", "env_int", "env_path"]
