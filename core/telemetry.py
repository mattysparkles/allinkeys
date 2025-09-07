"""Opt-in telemetry utilities.

This module collects aggregated runtime statistics and periodically sends them
to a central server. The data helps guide premium-tier offerings by revealing
popular hardware profiles and performance characteristics.

Only coarse metrics (GPU model names and keys-per-second rate) are gathered.
No seeds, addresses or personally identifying information are transmitted.
Telemetry can be disabled via the ``--no-telemetry`` CLI flag.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List

import requests

try:  # GPU info is optional
    import GPUtil  # type: ignore
except Exception:  # pragma: no cover - import failure shouldn't crash
    GPUtil = None  # type: ignore

# Endpoint can be overridden for testing via environment variable
TELEMETRY_URL = os.environ.get(
    "ALLINKEYS_TELEMETRY_URL",
    "https://telemetry.allinkeys.com/collect",
)


def gather_telemetry() -> Dict[str, object]:
    """Collect aggregated telemetry data.

    Returns a dictionary containing GPU model names and the current
    keys-per-second metric. This intentionally avoids any sensitive
    information such as seeds or addresses.
    """

    gpus: List[str] = []
    if GPUtil:
        try:
            gpus = [gpu.name for gpu in GPUtil.getGPUs()]
        except Exception:
            pass  # pragma: no cover - failure just results in empty list

    kps = 0.0
    try:
        from core.dashboard import get_metric

        kps = float(get_metric("keys_per_sec", 0) or 0)
    except Exception:
        pass  # pragma: no cover - metric subsystem not available

    return {"gpus": gpus, "kps": round(kps, 2)}


def send_telemetry(payload: Dict[str, object]) -> None:
    """Send telemetry payload to the central server.

    Network errors are ignored to ensure telemetry never interferes with core
    functionality.
    """

    try:
        requests.post(TELEMETRY_URL, json=payload, timeout=5)
    except Exception:
        pass  # pragma: no cover


def start_telemetry(shutdown_event, interval: int = 3600) -> None:
    """Start a background thread periodically sending telemetry.

    ``shutdown_event`` should be a :class:`threading.Event`-like object. The
    thread sends a payload immediately on start and again every ``interval``
    seconds until the event is set, at which point a final payload is sent.
    """

    def _loop() -> None:
        while not shutdown_event.is_set():
            payload = gather_telemetry()
            send_telemetry(payload)
            # Wait for either the interval to elapse or shutdown
            shutdown_event.wait(interval)
        # Send one last update capturing final metrics
        payload = gather_telemetry()
        send_telemetry(payload)

    threading.Thread(target=_loop, name="telemetry", daemon=True).start()

