"""OpenCL helper stubs for mnemonic mode.

The project aims to eventually accelerate parts of the mnemonic pipeline
using OpenCL.  This module provides a tiny facade around ``pyopencl`` so
that the rest of the code can query device availability without importing
``pyopencl`` directly at module import time.

The implementation intentionally avoids any heavy initialisation so that
systems without OpenCL can still run in CPU mode without crashing.
"""
from __future__ import annotations

from typing import List

try:  # pragma: no cover - optional dependency
    import pyopencl as cl  # type: ignore
except Exception:  # pragma: no cover - OpenCL not installed
    cl = None  # type: ignore


def available_devices() -> List[str]:
    """Return a list of available OpenCL device names."""
    if cl is None:
        return []
    names: List[str] = []
    try:
        for platform in cl.get_platforms():
            for device in platform.get_devices():
                names.append(device.name)
    except Exception:
        return []
    return names
