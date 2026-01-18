from __future__ import annotations

import threading
from typing import Any, Dict

MACHINE_REGISTRY: Dict[tuple[int, str], Dict[str, Any]] = {}
MACHINE_REGISTRY_LOCK = threading.Lock()
