import os
import re
import subprocess
import time
from typing import Dict, List, Tuple, Optional

from config.settings import (
    GPU_BACKEND,
    FORCE_CPU_FALLBACK,
    VANITYSEARCH_BIN_CUDA,
    VANITYSEARCH_BIN_OPENCL,
    VANITYSEARCH_BIN_CPU,
    MIN_EXPECTED_GPU_MKEYS,
    MAX_OUTPUT_FILE_SIZE,
)
from core.logger import get_logger
from core.dashboard import set_metric, update_dashboard_stat

logger = get_logger(__name__)

# throttle warning frequency
_LAST_WARN: Dict[str, float] = {}


def _warn_once(name: str, msg: str, interval: float = 30.0) -> None:
    """Emit ``msg`` at most once per ``interval`` seconds for the given ``name``."""
    now = time.time()
    if now - _LAST_WARN.get(name, 0) >= interval:
        logger.warning(msg)
        _LAST_WARN[name] = now


def _run_binary(binary: str, args: List[str]) -> str:
    try:
        return subprocess.check_output([binary] + args, stderr=subprocess.STDOUT, text=True)
    except Exception as exc:
        logger.debug(f"Device probe failed for {binary}: {exc}")
        return ""


def list_devices() -> Dict[str, List[Tuple[int, str]]]:
    """Return available GPU devices for CUDA and OpenCL binaries."""
    devices: Dict[str, List[Tuple[int, str]]] = {}
    binaries = {
        "cuda": VANITYSEARCH_BIN_CUDA,
        "opencl": VANITYSEARCH_BIN_OPENCL,
    }
    for backend, bin_path in binaries.items():
        if not os.path.exists(bin_path):
            continue
        out = _run_binary(bin_path, ["-l"])
        entries: List[Tuple[int, str]] = []
        for line in out.splitlines():
            m = re.search(r"#(\d+)\s+(.+)$", line)
            if m:
                entries.append((int(m.group(1)), m.group(2).strip()))
        if entries:
            devices[backend] = entries
    return devices


_SELECTED_BACKEND: str = "cpu"
_SELECTED_DEVICE_ID: Optional[int] = None
_SELECTED_DEVICE_NAME: str = "CPU"
_SELECTED_BINARY: str = VANITYSEARCH_BIN_CPU


def resolve_vanitysearch_binary(backend: str) -> str:
    """Return the VanitySearch binary path for ``backend``."""
    if backend == "cuda":
        return VANITYSEARCH_BIN_CUDA
    if backend == "opencl":
        return VANITYSEARCH_BIN_OPENCL
    return VANITYSEARCH_BIN_CPU


def probe_device() -> Tuple[str, Optional[int], str, str]:
    """Select appropriate backend/device based on settings and availability."""
    global _SELECTED_BACKEND, _SELECTED_DEVICE_ID, _SELECTED_DEVICE_NAME, _SELECTED_BINARY

    devices = list_devices()
    backend = "cpu"
    device_id: Optional[int] = None
    device_name = "CPU"

    if not FORCE_CPU_FALLBACK:
        if GPU_BACKEND in ("cuda", "opencl") and devices.get(GPU_BACKEND):
            backend = GPU_BACKEND
            device_id, device_name = devices[GPU_BACKEND][0]
        elif GPU_BACKEND == "auto":
            for cand in ("cuda", "opencl"):
                if devices.get(cand):
                    backend = cand
                    device_id, device_name = devices[cand][0]
                    break

    binary = resolve_vanitysearch_binary(backend)
    if backend in ("cuda", "opencl") and not os.path.exists(binary):
        _warn_once("binary_missing", f"GPU backend {backend} selected but binary missing. Falling back to CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        binary = resolve_vanitysearch_binary("cpu")

    if backend != "cpu" and FORCE_CPU_FALLBACK:
        _warn_once("cpu_forced", "GPU available but FORCE_CPU_FALLBACK=True; using CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        binary = resolve_vanitysearch_binary("cpu")

    if backend == "cpu" and GPU_BACKEND != "cpu":
        _warn_once("cpu_fallback", "GPU backend requested but CPU binary selected")

    _SELECTED_BACKEND = backend
    _SELECTED_DEVICE_ID = device_id
    _SELECTED_DEVICE_NAME = device_name
    _SELECTED_BINARY = binary

    update_dashboard_stat({
        "vanitysearch_backend": backend,
        "vanitysearch_device_name": device_name,
    })
    logger.info(
        f"VanitySearch device: {device_name} | backend: {backend} | binary: {binary} | FORCE_CPU_FALLBACK={FORCE_CPU_FALLBACK}"
    )
    return backend, device_id, device_name, binary


def run_vanitysearch(seed_args: List[str], output_path: str, device_id: Optional[int], backend: str,
                     timeout: int = 60, pause_event=None) -> bool:
    """Execute VanitySearch with live speed parsing and output capture."""
    binary = resolve_vanitysearch_binary(backend)
    cmd = [binary] + seed_args + ["-o", output_path]
    if backend in ("cuda", "opencl") and device_id is not None:
        cmd += ["-gpu", str(device_id)]
    logger.info(f"Executing: {' '.join(cmd)}")

    try:
        with open(output_path, "w", encoding="utf-8", buffering=1) as outfile:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            start = time.time()
            for line in proc.stdout:
                outfile.write(line)
                m = re.search(r"([0-9.]+)\s*([MK])?Key/s", line, re.IGNORECASE)
                if m:
                    speed = float(m.group(1))
                    if m.group(2) and m.group(2).upper() == "K":
                        speed /= 1000.0
                    update_dashboard_stat("vanitysearch_current_mkeys", round(speed, 2))
                    if backend != "cpu" and speed < MIN_EXPECTED_GPU_MKEYS:
                        _warn_once("low_speed", "Speed suggests CPU; check GPU selection")
                if pause_event and pause_event.is_set():
                    proc.terminate()
                if timeout and time.time() - start > timeout:
                    proc.terminate()
                if os.path.exists(output_path) and os.path.getsize(output_path) >= MAX_OUTPUT_FILE_SIZE:
                    proc.terminate()
            proc.wait()
    except Exception:
        logger.exception("Failed to execute VanitySearch")
        return False

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# Expose selected device info for callers
get_selected_backend = lambda: _SELECTED_BACKEND
get_selected_device_id = lambda: _SELECTED_DEVICE_ID
get_selected_device_name = lambda: _SELECTED_DEVICE_NAME
