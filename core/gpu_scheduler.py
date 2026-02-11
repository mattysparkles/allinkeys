"""GPU scheduling and dynamic reassignment utilities."""

import os
import multiprocessing
import threading
import time

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
    BACKLOG_MONITOR_INTERVAL_SECONDS,
)
try:
    from config.settings import (
        GPU_SWING_TARGET_BACKLOG_ETA,
        GPU_SWING_CLEARANCE_ETA,
        GPU_SWING_MIN_INTERVAL_SECONDS,
    )
except Exception:  # pragma: no cover - fallback for missing config
    GPU_SWING_TARGET_BACKLOG_ETA = None
    GPU_SWING_CLEARANCE_ETA = None
    GPU_SWING_MIN_INTERVAL_SECONDS = None
from config.directories import VANITY_OUTPUT_DIR
from core.logger import log_message
from utils.thread_guard import can_spawn_thread


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


def _parse_eta_seconds(value):
    if value in (None, "", "N/A"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("s"):
            stripped = stripped[:-1]
        parts = stripped.split(":")
        if len(parts) == 3:
            try:
                hrs, mins, secs = (int(part) for part in parts)
            except ValueError:
                return None
            return float(hrs * 3600 + mins * 60 + secs)
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def monitor_backlog_and_reassign(shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event=None):
    """Monitor backlog and toggle GPU assignments.

    Parameters
    ----------
    shared_metrics : dict-like
        Dictionary of shared dashboard metrics (Manager dict on Unix, local on Windows).
    vanity_flag : multiprocessing.Value
        1 if vanity_search should use GPU, else 0.
    altcoin_flag : multiprocessing.Value
        1 if altcoin_derive should use GPU, else 0.
    assignment_flag : multiprocessing.Value
        0=vanity,1=altcoin,2=split
    shutdown_event : multiprocessing.Event
        Optional event to stop the loop.
    """
    from core.worker_bootstrap import ensure_metrics_ready, _safe_set_metric
    # Ensure dashboard helpers have access to the shared metrics dict
    ensure_metrics_ready(shared_metrics)

    vendor, name = _detect_gpu_vendor()
    if name:
        log_message(
            f"[GPU Scheduler] ⚙️ Detected GPU: {name} (vendor={vendor}) using for altcoin derive.",
            "INFO",
            mode="gpu_scheduler",
        )
    else:
        log_message(
            "[GPU Scheduler] ⚠️ No compatible GPU detected, falling back to CPU.",
            "WARNING",
            mode="gpu_scheduler",
        )
        vanity_flag.value = 0
        altcoin_flag.value = 0
        _safe_set_metric("vanity_gpu_on", False)
        _safe_set_metric("altcoin_gpu_on", False)

    # Record the current scheduling strategy for the dashboard
    _safe_set_metric("gpu_strategy", "swing" if SWING_MODE else "static")

    stop_event = shutdown_event or threading.Event()

    poll_interval = max(1, int(BACKLOG_MONITOR_INTERVAL_SECONDS))
    last_backlog_count = None
    last_backlog_time = None
    last_switch_time = 0.0
    while not stop_event.is_set():
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
            backlog_count = len(backlog_files)
            _safe_set_metric("backlog_files_queued", backlog_count)

            now = time.time()
            backlog_growth_rate = 0.0
            if last_backlog_time is not None and last_backlog_count is not None:
                elapsed = max(now - last_backlog_time, 0.001)
                backlog_growth_rate = (backlog_count - last_backlog_count) / elapsed
            last_backlog_time = now
            last_backlog_count = backlog_count

            backlog_eta_seconds = None
            try:
                backlog_eta_seconds = _parse_eta_seconds(shared_metrics.get("backlog_eta"))
            except Exception:
                backlog_eta_seconds = None
            if backlog_eta_seconds is None:
                try:
                    backlog_eta_seconds = _parse_eta_seconds(
                        shared_metrics.get("backlog_avg_time")
                    )
                except Exception:
                    backlog_eta_seconds = None
                if backlog_eta_seconds is not None:
                    backlog_eta_seconds *= backlog_count

            drain_rate = max(0.0, -backlog_growth_rate)
            estimated_eta = None
            if backlog_eta_seconds is not None:
                estimated_eta = backlog_eta_seconds
            elif drain_rate > 0:
                estimated_eta = backlog_count / drain_rate

            keys_per_sec = 0.0
            try:
                keys_per_sec = float(shared_metrics.get("keys_per_sec", 0.0) or 0.0)
            except Exception:
                keys_per_sec = 0.0

            config_available = all(
                value is not None
                for value in (
                    GPU_SWING_TARGET_BACKLOG_ETA,
                    GPU_SWING_CLEARANCE_ETA,
                    GPU_SWING_MIN_INTERVAL_SECONDS,
                )
            )

            enable_altcoin = False
            disable_altcoin = False
            decision_reason = ""
            if config_available:
                if estimated_eta is not None:
                    if estimated_eta > GPU_SWING_TARGET_BACKLOG_ETA:
                        enable_altcoin = True
                        decision_reason = (
                            f"backlog ETA {estimated_eta:.1f}s "
                            f"> target {GPU_SWING_TARGET_BACKLOG_ETA}s"
                        )
                    elif estimated_eta < GPU_SWING_CLEARANCE_ETA:
                        disable_altcoin = True
                        decision_reason = (
                            f"backlog ETA {estimated_eta:.1f}s "
                            f"< clearance {GPU_SWING_CLEARANCE_ETA}s"
                        )
                elif backlog_growth_rate > 0 and keys_per_sec > 0:
                    enable_altcoin = True
                    decision_reason = (
                        f"backlog growing {backlog_growth_rate:+.2f}/s "
                        f"with {keys_per_sec:.2f} keys/s"
                    )
            else:
                if backlog_count >= 100:
                    enable_altcoin = True
                    decision_reason = "100+ backlog files (fallback threshold)"
                else:
                    disable_altcoin = True
                    decision_reason = "backlog under 100 files (fallback threshold)"

            # Safety net: large backlogs should swing even when ETA is missing or
            # misleadingly small. This prevents a single fast file from pinning
            # GPUs to vanity while thousands of backlog files accumulate.
            if backlog_count >= 100 and not enable_altcoin:
                enable_altcoin = True
                disable_altcoin = False
                decision_reason = "100+ backlog files (swing override)"
            elif backlog_count == 0 and not disable_altcoin and not enable_altcoin:
                disable_altcoin = True
                decision_reason = "backlog empty"

            can_switch = (now - last_switch_time) >= (
                GPU_SWING_MIN_INTERVAL_SECONDS or 0
            )

            if enable_altcoin and can_switch:
                if vanity_flag.value or not altcoin_flag.value or assignment_flag.value != 1:
                    vanity_flag.value = 0
                    altcoin_flag.value = 1
                    assignment_flag.value = 1
                    last_switch_time = now
                    log_message(
                        "[GPU Scheduler] 🚦 Switching GPUs to altcoin derive "
                        f"({decision_reason}).",
                        "INFO",
                        mode="gpu_scheduler",
                    )
                    _safe_set_metric("vanity_gpu_on", False)
                    _safe_set_metric("altcoin_gpu_on", True)
                    _safe_set_metric("gpu_assignment", "altcoin")
            elif disable_altcoin and can_switch:
                if not vanity_flag.value or altcoin_flag.value or assignment_flag.value != 0:
                    vanity_flag.value = 1
                    altcoin_flag.value = 0
                    assignment_flag.value = 0
                    last_switch_time = now
                    log_message(
                        "[GPU Scheduler] ✅ Switching GPUs back to vanity "
                        f"({decision_reason}).",
                        "INFO",
                        mode="gpu_scheduler",
                    )
                    _safe_set_metric("vanity_gpu_on", True)
                    _safe_set_metric("altcoin_gpu_on", False)
                    _safe_set_metric("gpu_assignment", "vanity")
        else:
            # Manual mode – respect dashboard assignments without forcing flags
            if assignment_flag.value != 2:
                assignment_flag.value = 2
            _safe_set_metric("gpu_assignment", "split")
            _safe_set_metric("vanity_gpu_on", bool(vanity_flag.value))
            _safe_set_metric("altcoin_gpu_on", bool(altcoin_flag.value))

        stop_event.wait(poll_interval)


class _SchedulerThreadAdapter:
    def __init__(self, thread: threading.Thread, shutdown_event) -> None:
        self._thread = thread
        self._shutdown_event = shutdown_event

    def join(self, timeout: float | None = None) -> None:
        try:
            self._thread.join(timeout)
        except RuntimeError:
            pass

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def terminate(self) -> None:
        try:
            self._shutdown_event.set()
        except Exception:
            pass


def start_scheduler(shared_metrics, shutdown_event):
    """Helper to spawn the scheduler in its own process.

    Returns (process-or-thread, vanity_flag, altcoin_flag, assignment_flag)
    """
    ctx = multiprocessing.get_context("spawn")
    vanity_flag = ctx.Value("i", 1)
    altcoin_flag = ctx.Value("i", 1)
    assignment_flag = ctx.Value("i", 0)
    if os.name == "nt":
        if not can_spawn_thread("gpu_scheduler"):
            log_message(
                "[GPU Scheduler] Thread launch skipped; thread limit reached",
                "WARNING",
                mode="gpu_scheduler",
            )
            return _SchedulerThreadAdapter(threading.Thread(target=lambda: None), shutdown_event), vanity_flag, altcoin_flag, assignment_flag
        thread = threading.Thread(
            target=monitor_backlog_and_reassign,
            args=(shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event),
            name="GPUScheduler",
            daemon=True,
        )
        thread.start()
        return _SchedulerThreadAdapter(thread, shutdown_event), vanity_flag, altcoin_flag, assignment_flag
    proc = ctx.Process(
        target=monitor_backlog_and_reassign,
        args=(shared_metrics, vanity_flag, altcoin_flag, assignment_flag, shutdown_event),
        name="GPUScheduler",
    )
    proc.daemon = True
    proc.start()
    return proc, vanity_flag, altcoin_flag, assignment_flag
