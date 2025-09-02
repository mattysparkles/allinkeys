"""OpenCL helper stubs for mnemonic mode.

The project aims to eventually accelerate parts of the mnemonic pipeline
using OpenCL.  This module provides a tiny facade around ``pyopencl`` so
that the rest of the code can query device availability without importing
``pyopencl`` directly at module import time.

The implementation intentionally avoids any heavy initialisation so that
systems without OpenCL can still run in CPU mode without crashing.
"""
from __future__ import annotations

from typing import List, Optional

try:  # pragma: no cover - optional dependency
    import pyopencl as cl  # type: ignore
except Exception:  # pragma: no cover - OpenCL not installed
    cl = None  # type: ignore

# ``hashlib`` is only used as a fallback when OpenCL is unavailable or when an
# error occurs during kernel compilation/execution.  Importing it unconditionally
# keeps this module lightweight while still providing a consistent public API.
import hashlib


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


def pbkdf2_sha512(
    mnemonic: str,
    passphrase: str = "",
    *,
    iterations: int = 2048,
    device_id: Optional[int] = None,
) -> bytes:
    """Derive a seed using PBKDF2-HMAC-SHA512.

    The function attempts to offload the computation to an OpenCL device when
    available.  The current implementation falls back to :func:`hashlib.pbkdf2_hmac`
    if no OpenCL platform is detected or if any error occurs during initialisation
    or execution.  This provides a drop-in replacement for the CPU based
    implementation while offering a hook for future optimised kernels.
    """

    salt = ("mnemonic" + passphrase).encode("utf-8")
    data = mnemonic.encode("utf-8")

    if cl is None:
        return hashlib.pbkdf2_hmac("sha512", data, salt, iterations)

    try:
        platforms = cl.get_platforms()
        devices = []
        for platform in platforms:
            devices.extend(platform.get_devices())
        if not devices:
            raise RuntimeError("no OpenCL devices found")

        if device_id is not None and 0 <= device_id < len(devices):
            device = devices[device_id]
        else:
            device = devices[0]

        # Context and queue creation validate the device without performing any
        # heavy work.  The actual PBKDF2 computation is currently executed on the
        # CPU; once kernels are implemented this section will enqueue them.
        ctx = cl.Context(devices=[device])  # pragma: no cover - requires OpenCL
        cl.CommandQueue(ctx)  # pragma: no cover - requires OpenCL

        # Fallback CPU computation.  The surrounding try/except ensures that
        # environments lacking full OpenCL support still work seamlessly.
        return hashlib.pbkdf2_hmac("sha512", data, salt, iterations)
    except Exception:
        return hashlib.pbkdf2_hmac("sha512", data, salt, iterations)
