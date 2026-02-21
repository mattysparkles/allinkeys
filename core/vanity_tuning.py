from __future__ import annotations

from typing import Optional, Sequence, Tuple

import psutil

from config.settings import (
    VANITYSEARCH_AUTOTUNE,
    VANITYSEARCH_GPU_THREADS,
    VANITYSEARCH_MAX_FOUND,
)
from core.logger import get_logger

try:
    import GPUtil
except Exception:  # pragma: no cover - optional dependency
    GPUtil = None

logger = get_logger(__name__)

# Conservative bounds for auto-tuned ``-m`` to avoid runaway memory usage.
_MAX_FOUND_MIN = 250_000
_MAX_FOUND_MAX = 5_000_000

_TUNING_CACHE: dict[tuple[bool, tuple[int, ...], Optional[str]], Tuple[Optional[int], Optional[int]]] = {}
_TUNING_LOGGED: set[tuple[bool, tuple[int, ...], Optional[str]]] = set()


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _gpu_memory_mb(gpu_ids: Sequence[int]) -> Optional[int]:
    if not GPUtil or not gpu_ids:
        return None
    try:
        gpus = GPUtil.getGPUs()
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.debug("GPUtil GPU query failed: %s", exc)
        return None
    wanted = set(gpu_ids)
    matches = [int(gpu.memoryTotal) for gpu in gpus if gpu.id in wanted]
    return max(matches) if matches else None


def _estimate_max_found() -> int:
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    # Roughly 125k entries per GiB keeps memory usage conservative.
    estimate = int(total_gb * 125_000)
    return _clamp(estimate, _MAX_FOUND_MIN, _MAX_FOUND_MAX)


def _resolve_max_found() -> Optional[int]:
    if VANITYSEARCH_MAX_FOUND is not None:
        return VANITYSEARCH_MAX_FOUND if VANITYSEARCH_MAX_FOUND > 0 else None
    if not VANITYSEARCH_AUTOTUNE:
        return None
    return _estimate_max_found()


def _resolve_gpu_threads(
    use_gpu: bool,
    gpu_ids: Sequence[int],
    backend: Optional[str],
) -> Optional[int]:
    if VANITYSEARCH_GPU_THREADS is not None:
        return VANITYSEARCH_GPU_THREADS if VANITYSEARCH_GPU_THREADS > 0 else None
    if not VANITYSEARCH_AUTOTUNE or not use_gpu:
        return None
    if backend and backend not in ("cuda", "opencl", "auto"):
        return None

    mem_mb = _gpu_memory_mb(gpu_ids)
    if mem_mb is None:
        return None

    # Keep grid sizes conservative for broader binary compatibility.
    if mem_mb >= 16000:
        return 1024
    if mem_mb >= 8000:
        return 512
    if mem_mb >= 6000:
        return 512
    if mem_mb >= 4000:
        return 256
    return 128


def resolve_vanitysearch_tuning(
    *,
    use_gpu: bool,
    gpu_ids: Optional[Sequence[int]] = None,
    backend: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int]]:
    key = (use_gpu, tuple(gpu_ids or ()), backend)
    if key in _TUNING_CACHE:
        return _TUNING_CACHE[key]

    max_found = _resolve_max_found()
    gpu_threads = _resolve_gpu_threads(use_gpu, gpu_ids or (), backend)
    _TUNING_CACHE[key] = (max_found, gpu_threads)

    if key not in _TUNING_LOGGED:
        logger.info(
            "VanitySearch tuning: max_found=%s gpu_threads=%s auto=%s",
            max_found if max_found is not None else "default",
            gpu_threads if gpu_threads is not None else "default",
            VANITYSEARCH_AUTOTUNE,
        )
        _TUNING_LOGGED.add(key)

    return max_found, gpu_threads


def apply_vanitysearch_tuning_args(
    args: Sequence[str],
    *,
    use_gpu: bool,
    gpu_ids: Optional[Sequence[int]] = None,
    backend: Optional[str] = None,
) -> list[str]:
    """Return args with optional ``-m`` and ``-g`` tuning flags applied."""
    tuned = list(args)
    max_found, gpu_threads = resolve_vanitysearch_tuning(
        use_gpu=use_gpu,
        gpu_ids=gpu_ids,
        backend=backend,
    )

    if max_found is not None and "-m" not in tuned:
        tuned += ["-m", str(max_found)]
    if use_gpu and gpu_threads is not None and "-g" not in tuned:
        tuned += ["-g", str(gpu_threads)]

    return tuned
