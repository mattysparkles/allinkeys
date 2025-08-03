"""GPU scheduling and dynamic reassignment utilities."""

import time
import multiprocessing

try:
    import pyopencl as cl
except Exception:  # pragma: no cover
    cl = None

try:
    import pynvml
except Exception:  # pragma: no cover
    pynvml = None

from config.settings import (
    GPU_STRATEGY,
    MAX_BACKLOG_THRESHOLD,
    MIN_BACKLOG_THRESHOLD,
    GPU_VENDOR,
)
from core.logger import log_message
from core.dashboard import set_metric


def _detect_gpu_vendor():
    """Return tuple (vendor, name) or (None, None) if no GPU found."""
    if GPU_VENDOR.lower() in {"nvidia", "amd"}:
        return GPU_VENDOR.lower(), GPU_VENDOR.lower()
    # Try NVML first for NVIDIA
    if pynvml:
        try:
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle).decode()
            return "nvidia", name
        except Exception:
            pass
    if cl:
        try:
            for platform in cl.get_platforms():
                if "NVIDIA" in platform.name.upper():
                    return "nvidia", platform.name
                if "AMD" in platform.name.upper():
                    return "amd", platform.name
        except Exception:
            pass
    return None, None


def monitor_backlog_and_reassign(shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event=None):
    """Monitor backlog and toggle GPU assignments.

    Parameters
    ----------
    shared_metrics : multiprocessing.Manager().dict
        Dictionary of shared dashboard metrics.
    vanity_flag : multiprocessing.Value
        1 if vanity_search should use GPU, else 0.
    altcoin_flag : multiprocessing.Value
        1 if altcoin_derive should use GPU, else 0.
    assignment_flag : multiprocessing.Value
        0=vanity,1=altcoin,2=split
    shutdown_event : multiprocessing.Event
        Optional event to stop the loop.
    """
    vendor, name = _detect_gpu_vendor()
    if name:
        log_message(
            f"[GPU Scheduler] ⚙️ Detected GPU: {name} (vendor={vendor}) using for altcoin derive.",
            "INFO",
        )
    else:
        log_message(
            "[GPU Scheduler] ⚠️ No compatible GPU detected, falling back to CPU.",
            "WARNING",
        )
        vanity_flag.value = 0
        altcoin_flag.value = 0
        set_metric("vanity_gpu_on", False)
        set_metric("altcoin_gpu_on", False)

    strategy = GPU_STRATEGY
    set_metric("gpu_strategy", strategy)

    while shutdown_event is None or not shutdown_event.is_set():
        try:
            strategy = shared_metrics.get("gpu_strategy", strategy)
        except Exception:
            pass

        if strategy == "vanity_priority":
            if not vanity_flag.value or altcoin_flag.value:
                vanity_flag.value = 1
                altcoin_flag.value = 0
                assignment_flag.value = 0
                set_metric("vanity_gpu_on", True)
                set_metric("altcoin_gpu_on", False)
                set_metric("gpu_assignment", "vanity")
            time.sleep(2)
            continue

        if strategy == "csv_priority":
            if vanity_flag.value or not altcoin_flag.value:
                vanity_flag.value = 0
                altcoin_flag.value = 1
                assignment_flag.value = 1
                set_metric("vanity_gpu_on", False)
                set_metric("altcoin_gpu_on", True)
                set_metric("gpu_assignment", "altcoin")
            time.sleep(2)
            continue

        # Swing mode dynamic loop
        try:
            backlog_size = shared_metrics.get("backlog_files_queued", 0)
        except Exception:
            backlog_size = 0
        if backlog_size > MAX_BACKLOG_THRESHOLD:
            if vanity_flag.value:
                vanity_flag.value = 0
                altcoin_flag.value = 1
                assignment_flag.value = 1
                log_message(
                    f"[GPU Scheduler] 🚦 Backlog exceeded {MAX_BACKLOG_THRESHOLD} — pausing vanity GPU, prioritizing backlog processing...",
                    "INFO",
                )
                set_metric("vanity_gpu_on", False)
                set_metric("altcoin_gpu_on", True)
                set_metric("gpu_assignment", "altcoin")
        elif backlog_size <= MIN_BACKLOG_THRESHOLD:
            if not vanity_flag.value:
                vanity_flag.value = 1
                altcoin_flag.value = 1
                assignment_flag.value = 0
                log_message(
                    "[GPU Scheduler] ✅ Backlog reduced — resuming vanity GPU usage...",
                    "INFO",
                )
                set_metric("vanity_gpu_on", True)
                set_metric("altcoin_gpu_on", True)
                set_metric("gpu_assignment", "vanity")
        time.sleep(2)


def start_scheduler(shared_metrics, shutdown_event):
    """Helper to spawn the scheduler in its own process.

    Returns (process, vanity_flag, altcoin_flag, assignment_flag)
    """
    ctx = multiprocessing.get_context("spawn")
    vanity_flag = ctx.Value("i", 1)
    altcoin_flag = ctx.Value("i", 1)
    assignment_flag = ctx.Value("i", 0)
    proc = ctx.Process(
        target=monitor_backlog_and_reassign,
        args=(shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event),
        name="GPUScheduler",
    )
    proc.daemon = True
    proc.start()
    return proc, vanity_flag, altcoin_flag, assignment_flag
