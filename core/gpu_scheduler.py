"""GPU scheduling and dynamic reassignment utilities."""

import time
import os
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
    GPU_VENDOR,
    VANITY_OUTPUT_DIR,
)
from core.logger import log_message
from core.dashboard import set_metric, init_shared_metrics


# Alias VanitySearch output directory as the input backlog for altcoin derive
ALTCOIN_INPUT_DIR = VANITY_OUTPUT_DIR
# Determine swing mode from the configured GPU strategy
SWING_MODE = GPU_STRATEGY == "swing"


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
    # Ensure dashboard helpers have access to the shared metrics dict
    init_shared_metrics(shared_metrics)

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

    # Record the current scheduling strategy for the dashboard
    set_metric("gpu_strategy", "swing" if SWING_MODE else "static")

    while shutdown_event is None or not shutdown_event.is_set():
        try:
            swing_mode = shared_metrics.get("swing_mode", SWING_MODE)
        except Exception:
            swing_mode = SWING_MODE

        if swing_mode:
            try:
                backlog_files = [
                    f for f in os.listdir(ALTCOIN_INPUT_DIR) if f.endswith(".txt")
                ]
            except Exception:
                backlog_files = []
            set_metric("backlog_files_queued", len(backlog_files))

            if backlog_files:
                if vanity_flag.value or not altcoin_flag.value or assignment_flag.value != 1:
                    vanity_flag.value = 0
                    altcoin_flag.value = 1
                    assignment_flag.value = 1
                    log_message(
                        "[GPU Scheduler] 🚦 Altcoin backlog detected — switching GPU to altcoin derive...",
                        "INFO",
                    )
                    set_metric("vanity_gpu_on", False)
                    set_metric("altcoin_gpu_on", True)
                    set_metric("gpu_assignment", "altcoin")
            else:
                if not vanity_flag.value or altcoin_flag.value or assignment_flag.value != 0:
                    vanity_flag.value = 1
                    altcoin_flag.value = 0
                    assignment_flag.value = 0
                    log_message(
                        "[GPU Scheduler] ✅ Altcoin backlog empty — resuming vanity GPU usage...",
                        "INFO",
                    )
                    set_metric("vanity_gpu_on", True)
                    set_metric("altcoin_gpu_on", False)
                    set_metric("gpu_assignment", "vanity")
        else:
            if assignment_flag.value != 2:
                assignment_flag.value = 2
                set_metric("gpu_assignment", "split")
                set_metric("vanity_gpu_on", bool(vanity_flag.value))
                set_metric("altcoin_gpu_on", bool(altcoin_flag.value))

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
