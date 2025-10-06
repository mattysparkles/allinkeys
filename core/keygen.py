# core/keygen.py

import os
from pathlib import Path
import time
import subprocess
import threading
import secrets
import platform
import re
from datetime import datetime
from collections import deque
from threading import Lock

from config.settings import (
    VANITYSEARCH_PATH,
    VANITY_PATTERN,
    MAX_OUTPUT_FILE_SIZE,
    MAX_OUTPUT_LINES,
    ROTATE_INTERVAL_SECONDS,
    ROTATE_MAX_WAIT_SECONDS,
    FILES_PER_BATCH,
)
from config.directories import VANITY_OUTPUT_DIR
from config.constants import SECP256K1_ORDER
from core.checkpoint import (
    load_keygen_checkpoint as load_checkpoint,
    save_keygen_checkpoint as save_checkpoint,
)
from core.gpu_selector import (
    get_vanitysearch_gpu_ids,
)  # ✅ Correct GPU selection integration
from core.logger import get_logger
import config.settings as settings
from core.seed_tracker import (
    seed_in_used_range,
    record_seed_range,
    get_condensed_ranges,
)
from core.telemetry import check_seed_seen, record_seed_event

# Runtime trackers / metrics window
total_keys_generated = 0
keygen_start_time = time.time()
last_output_file = None
KPS_WINDOW = deque()

# Prefetch queue to reduce I/O when checking used seeds
_SEED_QUEUE: deque[int] = deque()
_SEED_QUEUE_LOCK = Lock()
SEED_QUEUE_SIZE = 100
SEED_QUEUE_MAX_ATTEMPTS = SEED_QUEUE_SIZE * 25


def _seed_in_ranges(seed: int, ranges) -> bool:
    """Return True if ``seed`` falls within any condensed ``ranges``."""

    for start, end in ranges:
        if start <= seed <= end:
            return True
    return False


def telemetry_enabled() -> bool:
    """Return ``True`` when experimental telemetry features are active."""

    return bool(
        getattr(settings, "ENABLE_TELEMETRY", False)
        and getattr(settings, "SEED_TELEMETRY_ENABLED", False)
    )

# Batch progress
KEYGEN_STATE = {
    "batch_id": 0,
    "index_within_batch": 0,
    "last_seed": None,
}

# Centralized logger
logger = get_logger("keygen")

# BTC address format toggle (compressed by default)
BTC_COMPRESSED = True


# ---------------------------------------------------------------------------
# Public helpers / status
# ---------------------------------------------------------------------------


def run_btc_only(
    compressed: bool,
    shared_metrics=None,
    shutdown_event=None,
    pause_event=None,
    gpu_flag=None,
) -> int:
    """
    Run VanitySearch in BTC-only mode using the same loop as the main app,
    but without spawning extra processes. Returns 0 on clean exit; 1 on error.
    """
    global BTC_COMPRESSED
    BTC_COMPRESSED = bool(compressed)
    mode = "compressed" if BTC_COMPRESSED else "uncompressed"
    logger.info(f"🔑 BTC-only keygen starting (format={mode})")

    try:
        start_keygen_loop(
            shared_metrics=shared_metrics,
            shutdown_event=shutdown_event,
            pause_event=pause_event,
            gpu_flag=gpu_flag,
        )
    except Exception:
        logger.exception("BTC-only keygen terminated due to an unexpected error")
        return 1
    return 0


def keygen_progress():
    """
    Return a dict of current keygen status for the GUI/dashboard.
    """
    elapsed_seconds = max(1, int(time.time() - keygen_start_time))
    elapsed_time_str = str(
        datetime.utcfromtimestamp(elapsed_seconds).strftime("%H:%M:%S")
    )
    if len(KPS_WINDOW) >= 2:
        keys_per_sec = (KPS_WINDOW[-1][1] - KPS_WINDOW[0][1]) / max(
            1e-6, KPS_WINDOW[-1][0] - KPS_WINDOW[0][0]
        )
    else:
        keys_per_sec = 0.0
    return {
        "total_keys_generated": total_keys_generated,
        "current_batch_id": KEYGEN_STATE["batch_id"],
        "index_within_batch": KEYGEN_STATE["index_within_batch"],
        "last_seed": KEYGEN_STATE["last_seed"],
        "elapsed_time": elapsed_time_str,
        "start_timestamp": datetime.utcfromtimestamp(keygen_start_time).isoformat()
        + "Z",
        "keys_per_sec": round(keys_per_sec, 2),
    }


# ---------------------------------------------------------------------------
# Seed utilities
# ---------------------------------------------------------------------------


def generate_seed_from_batch(batch_id, index_within_batch, batch_size=1_024_000):
    """
    Deterministically derive a seed from (batch_id, index) while staying in range.
    """
    seed = batch_id * batch_size + index_within_batch
    min_val = 1 << 128
    if seed < min_val:
        seed += min_val
    if seed >= SECP256K1_ORDER:
        return None
    return seed


def _telemetry_context():
    """Return (mode, range_id) tuple for telemetry labeling."""
    if not telemetry_enabled():
        return "vanity", "default"
    if getattr(settings, "PUZZLE_MODE", False):
        num = getattr(settings, "PUZZLE_NUMBER", None)
        return "puzzle", (f"puzzle-{num}" if num is not None else "puzzle")
    return "vanity", "default"


def _central_seen(seed: int) -> bool:
    mode, range_id = _telemetry_context()
    if not telemetry_enabled():
        return False
    try:
        return check_seed_seen(
            int(seed).to_bytes(32, "big"), mode=mode, range_id=range_id
        )
    except Exception:
        return False


def _prefill_seed_queue(min_bits: int = 128) -> None:
    """Populate the local seed queue up to ``SEED_QUEUE_SIZE`` entries."""

    if getattr(settings, "PUZZLE_MODE", False):
        return

    with _SEED_QUEUE_LOCK:
        if len(_SEED_QUEUE) >= SEED_QUEUE_SIZE:
            return

        min_val = 1 << min_bits
        range_span = SECP256K1_ORDER - min_val
        ranges = get_condensed_ranges()
        attempts = 0

        while len(_SEED_QUEUE) < SEED_QUEUE_SIZE and attempts < SEED_QUEUE_MAX_ATTEMPTS:
            attempts += 1
            candidate = secrets.randbelow(range_span) + min_val
            if _seed_in_ranges(candidate, ranges):
                continue

            if telemetry_enabled():
                seen_centrally = _central_seen(candidate)
                if seen_centrally:
                    try:
                        mode, range_id = _telemetry_context()
                        record_seed_event(
                            int(candidate).to_bytes(32, "big"),
                            mode=mode,
                            range_id=range_id,
                            used=True,
                            match_found=False,
                        )
                    except Exception:
                        pass
                    continue

            _SEED_QUEUE.append(candidate)


def generate_random_seed(min_bits=128):
    """Generate the next seed for VanitySearch while avoiding used ranges."""

    while True:
        if getattr(settings, "PUZZLE_MODE", False):
            puzzle_num = getattr(settings, "PUZZLE_NUMBER", None)
            if puzzle_num is not None:
                from core import puzzle_queue as pq  # depends on SQLite

                chunk_idx = getattr(settings, "PUZZLE_CHUNK_INDEX", None)
                host_id = platform.node() if hasattr(platform, "node") else "unknown"
                seed = pq.next_seed(puzzle_num, host_id, chunk_idx)
                if seed is None:
                    raise RuntimeError(
                        f"No remaining puzzle-{puzzle_num} chunks to process"
                    )
                if seed_in_used_range(seed):
                    continue
                if telemetry_enabled() and _central_seen(seed):
                    try:
                        record_seed_event(
                            int(seed).to_bytes(32, "big"),
                            mode=_telemetry_context()[0],
                            range_id=_telemetry_context()[1],
                            used=True,
                            match_found=False,
                        )
                    except Exception:
                        pass
                    continue
                return seed

            start = int(getattr(settings, "PUZZLE_START", "0"), 16)
            end = int(getattr(settings, "PUZZLE_END", "0"), 16)
            if end < start:
                start, end = end, start
            span = max(1, end - start + 1)
            seed = secrets.randbelow(span) + start
            if seed_in_used_range(seed):
                continue
            if telemetry_enabled() and _central_seen(seed):
                try:
                    record_seed_event(
                        int(seed).to_bytes(32, "big"),
                        mode=_telemetry_context()[0],
                        range_id=_telemetry_context()[1],
                        used=True,
                        match_found=False,
                    )
                except Exception:
                    pass
                continue
            return seed

        if not _SEED_QUEUE:
            _prefill_seed_queue(min_bits=min_bits)
            if not _SEED_QUEUE:
                time.sleep(0.05)
                continue

        with _SEED_QUEUE_LOCK:
            try:
                seed = _SEED_QUEUE.popleft()
            except IndexError:
                seed = None

        if seed is None:
            time.sleep(0.05)
            continue

        if seed_in_used_range(seed):
            continue

        return seed


# ---------------------------------------------------------------------------
# VanitySearch output parsing
# ---------------------------------------------------------------------------


def parse_vanity_file(path):
    """Return number of keys and first/last seed from a VanitySearch file.

    Supports both ``Priv (HEX):`` (VanitySearch) and ``Privkey:`` (oclvanity*)
    line formats. Falls back to a regex search and skips malformed lines.
    """

    lines = 0
    first_seed = None
    last_seed = None
    pattern = re.compile(r"(?i)priv(?:key)?(?: \(hex\))?:\s*(?:0x)?([0-9a-f]+)")

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                hex_part = None
                if line.startswith("Priv (HEX):") or line.startswith("Privkey:"):
                    hex_part = line.split(":", 1)[1].strip()
                else:
                    m = pattern.search(line)
                    if m:
                        hex_part = m.group(1)
                if not hex_part:
                    continue
                hex_part = hex_part.replace("0x", "")
                try:
                    seed_int = int(hex_part, 16)
                except ValueError:
                    continue
                if first_seed is None:
                    first_seed = seed_int
                last_seed = seed_int
                lines += 1
    except FileNotFoundError:
        return 0, None, None

    return lines, first_seed, last_seed


# ---------------------------------------------------------------------------
# VanitySearch runner (single-file run + rotation)
# ---------------------------------------------------------------------------


def run_vanitysearch_stream(
    initial_seed_int, batch_id, index_within_batch, pause_event=None, gpu_flag=None
):
    """
    Run VanitySearch once. VanitySearch writes the output via `-o <file>`.
    We monitor the file for rotation triggers (time/size/line-count) and update metrics.
    Returns True if a non-empty output file exists at the end; False otherwise.
    """
    global total_keys_generated, last_output_file

    # Enforce active puzzle range before any heavy work.  When running in puzzle
    # mode the seed must stay within the configured [start, end] bounds.  If a
    # caller somehow provides an out-of-range seed we simply skip processing,
    # incrementing a diagnostic metric for the dashboard.  This check occurs
    # early so we avoid spawning VanitySearch or touching the filesystem.
    if getattr(settings, "PUZZLE_MODE", False):
        start = int(getattr(settings, "PUZZLE_START", "0"), 16)
        end = int(getattr(settings, "PUZZLE_END", "0"), 16)
        if not (start <= initial_seed_int <= end):
            logger.debug(
                "Seed %x outside puzzle range [%x, %x] — skipping",
                initial_seed_int,
                start,
                end,
            )
            increment_metric("out_of_range_skipped", 1)
            return False

    # GPU runtime toggle exposed to the GUI
    use_gpu = True if gpu_flag is None else bool(gpu_flag.value)
    selected_gpu_ids = get_vanitysearch_gpu_ids() if use_gpu else []
    gpu_env = (
        {"CUDA_VISIBLE_DEVICES": ",".join(str(i) for i in selected_gpu_ids)}
        if selected_gpu_ids
        else {}
    )

    # Seed formatting
    hex_seed_full = hex(initial_seed_int)[2:].rjust(64, "0")
    hex_seed_short = hex(initial_seed_int)[2:].lstrip("0")[:8] or "00000000"

    # Output path (VanitySearch owns this file via -o)
    current_output_path = os.path.join(
        VANITY_OUTPUT_DIR,
        f"batch_{batch_id}_part_{index_within_batch}_seed_{hex_seed_short}.txt",
    )
    temp_output_path = current_output_path + ".part"
    last_output_file = current_output_path

    # Ensure we start from a clean slate so previous runs do not bleed into the
    # new VanitySearch execution. If a stale file exists we remove it first.
    try:
        if os.path.exists(current_output_path):
            os.remove(current_output_path)
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
    except OSError:
        logger.warning(
            "⚠️ Unable to remove stale VanitySearch output %s before launch",
            current_output_path,
            exc_info=True,
        )

    # Normalize pattern and ensure it's final positional arg
    pattern = str(VANITY_PATTERN).strip()
    if (pattern.startswith('"') and pattern.endswith('"')) or (
        pattern.startswith("'") and pattern.endswith("'")
    ):
        pattern = pattern[1:-1].strip()
    if not pattern:
        pattern = "1**"

    # Resolve VanitySearch binary and build command (all args as str)
    exe_path = str(VANITYSEARCH_PATH)
    if not exe_path or not os.path.exists(exe_path):
        logger.error("VanitySearch binary not found: %s", exe_path)
        raise FileNotFoundError("VanitySearch binary not found.")

    cmd = [exe_path, "-s", str(hex_seed_full), "-o", str(temp_output_path)]
    if use_gpu:
        cmd.append("-gpu")  # CUDA acceleration
    if not BTC_COMPRESSED:
        cmd.append("-u")  # uncompressed WIF
    cmd.append(pattern)  # <<< MUST be last

    cmd_preview = " ".join(map(str, cmd))
    logger.info(
        f"🧬 Starting VanitySearch:\n"
        f"   Seed: {hex_seed_full}\n"
        f"   Output: {current_output_path}\n"
        f"   GPUs: {selected_gpu_ids or 'CPU'}\n"
        f"   Pattern: {pattern}"
    )
    logger.info(f"🚀 Running command: {cmd_preview}")

    # Respect pause before launch
    if pause_event and pause_event.is_set():
        logger.info("⏸️ Pause detected before launch. Skipping VanitySearch run.")
        return False

    # Launch and monitor (VanitySearch writes file via -o; we do NOT open it)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,  # do not tee into the same file
            stderr=subprocess.STDOUT,
            env={**os.environ, **gpu_env},
        )
        logger.info(f"Spawned VanitySearch PID {proc.pid} with args {cmd_preview}")

        def monitor_process(p, path):
            """Monitor file size/lines and pause requests while VanitySearch runs."""

            start = time.time()
            logger.info("[Rotation] Monitor started for %s", os.path.basename(path))
            last_tick = 0
            while p.poll() is None:
                if pause_event and pause_event.is_set():
                    logger.info(
                        "⏸️ Pause requested. Terminating VanitySearch process...",
                    )
                    p.terminate()
                    break
                # Heartbeat every 5s so we can see rotation timer progress
                elapsed = int(time.time() - start)
                if elapsed // 5 > last_tick // 5:
                    logger.info("[Rotation] Elapsed=%ss / Interval=%ss", elapsed, ROTATE_INTERVAL_SECONDS)
                    last_tick = elapsed
                if time.time() - start >= ROTATE_INTERVAL_SECONDS:
                    logger.info(
                        f"⏱️ Rotation interval reached ({ROTATE_INTERVAL_SECONDS}s). Terminating for rotation.",
                    )
                    p.terminate()
                    break
                try:
                    if os.path.exists(path):
                        size_now = os.path.getsize(path)
                        if size_now >= MAX_OUTPUT_FILE_SIZE:
                            logger.info(
                                f"📏 Size threshold {size_now}/{MAX_OUTPUT_FILE_SIZE} bytes reached. Rotating {os.path.basename(path)}"
                            )
                            p.terminate()
                            break
                        # Line-based rotation is expensive; guard errors from Windows file locks
                        try:
                            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                                # Use a lightweight line count pass. If it takes too long it will pick up next tick.
                                line_count = sum(1 for _ in f)
                            if line_count >= MAX_OUTPUT_LINES:
                                logger.info(
                                    f"📏 Line threshold {line_count}/{MAX_OUTPUT_LINES} reached. Rotating {os.path.basename(path)}"
                                )
                                p.terminate()
                                break
                        except (FileNotFoundError, PermissionError, OSError):
                            # File may be locked by writer on Windows; try again next tick
                            pass
                except (FileNotFoundError, PermissionError, OSError):
                    logger.debug("Output not ready or locked during monitoring; retrying")
                time.sleep(1)

        timer_thread = threading.Thread(
            target=monitor_process, args=(proc, temp_output_path)
        )
        timer_thread.start()
        wait_timeout = (
            ROTATE_MAX_WAIT_SECONDS
            if ROTATE_MAX_WAIT_SECONDS and ROTATE_MAX_WAIT_SECONDS > 0
            else None
        )
        try:
            proc.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "⚠️ VanitySearch did not exit within %ss after terminate; killing.",
                ROTATE_MAX_WAIT_SECONDS,
            )
            try:
                proc.kill()
            finally:
                proc.wait()
        finally:
            timer_thread.join()
    except Exception as e:
        logger.exception(f"Failed to execute VanitySearch: {e}")
        return False

    # Post-process output file: use parser; do not delete zero-byte files
    if os.path.exists(temp_output_path):
        try:
            Path(temp_output_path).replace(current_output_path)
        except Exception:
            logger.warning(
                "⚠️ Failed to finalize VanitySearch output %s", temp_output_path,
                exc_info=True,
            )
            return False

    if os.path.exists(current_output_path):
        size = os.path.getsize(current_output_path)
        if size == 0:
            logger.warning(f"⚠️ Output file empty: {current_output_path}")
            os.remove(current_output_path)
            return True

        try:
            lines, first_seed, last_seed = parse_vanity_file(current_output_path)

            from core.dashboard import update_dashboard_stat, get_metric

            total_keys_generated += lines
            increment_metric("keys_generated_today", lines)
            increment_metric("keys_generated_lifetime", lines)
            update_dashboard_stat(
                "keys_generated_today", get_metric("keys_generated_today")
            )
            update_dashboard_stat(
                "keys_generated_lifetime", get_metric("keys_generated_lifetime")
            )
            if first_seed is not None and last_seed is not None:
                record_seed_range(first_seed, last_seed)

            if telemetry_enabled():
                try:
                    mode, range_id = _telemetry_context()
                    record_seed_event(
                        int(initial_seed_int).to_bytes(32, "big"),
                        mode=mode,
                        range_id=range_id,
                        used=True,
                        match_found=False,
                    )
                except Exception:
                    pass

            logger.info(f"📄 File complete: {lines} lines → {current_output_path}")
            try:
                set_metric("last_rotation", time.time())
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse {current_output_path}: {e}")
        return True

    logger.error(f"❌ Output file not created: {current_output_path}")
    return True


# Dashboard metric helpers (imported here to keep module import order)
from core.dashboard import (  # noqa: E402
    init_shared_metrics,
    set_metric,
    increment_metric,
    get_metric,
)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def start_keygen_loop(
    shared_metrics=None, shutdown_event=None, pause_event=None, gpu_flag=None
):
    """
    Main keygen loop:
    - Initializes dashboard & control events
    - Ensures output directory exists
    - Steps through FILES_PER_BATCH files per batch, rotating outputs via runner
    - Saves checkpoints for mid-batch resume
    """
    # Metrics shared memory init
    try:
        init_shared_metrics(shared_metrics)
        logger.debug(f"Shared metrics initialized for {__name__}")
    except Exception as e:
        logger.exception(f"init_shared_metrics failed in {__name__}: {e}")

    # Control events for GUI
    from core.dashboard import register_control_events

    register_control_events(shutdown_event, pause_event, module="keygen")

    # Ensure output directory exists
    if not Path(VANITY_OUTPUT_DIR).exists():
        Path(VANITY_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Warm the seed queue so the first rotation does not block on telemetry
    _prefill_seed_queue()

    # Puzzle mode queue prepare (deterministic ranges)
    if (
        getattr(settings, "PUZZLE_MODE", False)
        and getattr(settings, "PUZZLE_NUMBER", None) is not None
    ):
        from core import puzzle_queue as pq

        pq.init_work_queue()

    # Load or randomize starting batch/index
    checkpoint = load_checkpoint()
    if checkpoint:
        KEYGEN_STATE["batch_id"] = checkpoint.get("batch_id", 0)
        KEYGEN_STATE["index_within_batch"] = checkpoint.get("index_within_batch", 0)
        logger.info("✅ Checkpoint loaded successfully")
    else:
        KEYGEN_STATE["batch_id"] = secrets.randbelow(1_000_000)
        KEYGEN_STATE["index_within_batch"] = secrets.randbelow(FILES_PER_BATCH)
        logger.info("🚀 No checkpoint found. Starting with randomized batch/index.")

    # Initialize dashboard metrics so the GUI never shows N/A
    set_metric("keys_generated_today", 0)
    set_metric("vanity_progress_percent", 0)
    set_metric("current_seed_index", KEYGEN_STATE["index_within_batch"])
    set_metric("current_seed", KEYGEN_STATE.get("last_seed", "0x0"))
    if getattr(settings, "PUZZLE_MODE", False):
        # Track seeds discarded for falling outside the active puzzle range.
        set_metric("out_of_range_skipped", 0)

    try:
        set_metric("status.keygen", "Running")
        from core.dashboard import (
            set_thread_health,
            get_shutdown_event,
            get_pause_event,
        )

        set_thread_health("keygen", True)

        shutdown_evt = get_shutdown_event("keygen")
        pause_evt = get_pause_event("keygen")

        batches_completed = 0
        total_time = 0.0
        pause_logged = False
        pause_log_ts = 0.0

        while True:
            if shutdown_evt and shutdown_evt.is_set():
                break

            if pause_evt and pause_evt.is_set():
                # Emit a heartbeat log every 5s while paused so the user knows it's alive
                if (not pause_logged) or (time.time() - pause_log_ts > 5):
                    logger.info("⏸️ Keygen paused. Waiting to resume...")
                    pause_logged = True
                    pause_log_ts = time.time()
                time.sleep(1)
                continue
            elif pause_logged:
                logger.info("▶️ Keygen resumed.")
                pause_logged = False

            # update keys/sec using a moving window of the last 5 seconds
            now = time.time()
            current_keys = get_metric("keys_generated_today", 0)
            KPS_WINDOW.append((now, current_keys))
            while KPS_WINDOW and now - KPS_WINDOW[0][0] > 5:
                KPS_WINDOW.popleft()
            if len(KPS_WINDOW) >= 2:
                kps = (current_keys - KPS_WINDOW[0][1]) / (now - KPS_WINDOW[0][0])
            else:
                kps = 0.0
            set_metric("keys_per_sec", round(kps, 2))

            # Run one batch worth of files
            batch_start = time.perf_counter()
            index = KEYGEN_STATE["index_within_batch"]
            while index < FILES_PER_BATCH:
                if shutdown_evt and shutdown_evt.is_set():
                    break
                if pause_evt and pause_evt.is_set():
                    # Inner-loop pause check to halt new VanitySearch runs
                    set_metric("keys_per_sec", 0)
                    time.sleep(1)
                    continue

                seed = generate_random_seed()
                KEYGEN_STATE["index_within_batch"] = index
                KEYGEN_STATE["last_seed"] = hex(seed)[2:].rjust(64, "0")
                set_metric("current_seed", KEYGEN_STATE["last_seed"])
                set_metric("current_seed_index", index)
                progress = round((index / float(FILES_PER_BATCH)) * 100, 2)
                set_metric("vanity_progress_percent", progress)

                # Telemetry: record that we are using this seed (post-skip phase)
                if telemetry_enabled():
                    try:
                        mode, range_id = _telemetry_context()
                        record_seed_event(
                            int(seed).to_bytes(32, "big"),
                            mode=mode,
                            range_id=range_id,
                            used=False,
                            match_found=False,
                        )
                    except Exception:
                        pass

                success = run_vanitysearch_stream(
                    seed, KEYGEN_STATE["batch_id"], index, pause_evt, gpu_flag
                )
                if not success:
                    time.sleep(1)
                    continue

                # Save after each file so progress can resume mid-batch
                try:
                    logger.info(
                        f"[Rotation] Completed part {index} of batch {KEYGEN_STATE['batch_id']}; starting next file"
                    )
                except Exception:
                    pass
                save_checkpoint(
                    {
                        "batch_id": KEYGEN_STATE["batch_id"],
                        "index_within_batch": index + 1,
                    }
                )
                index += 1

            batch_end = time.perf_counter()
            batches_completed += 1
            total_time += batch_end - batch_start
            set_metric("batches_completed", batches_completed)
            set_metric("avg_keygen_time", round(total_time / batches_completed, 2))
            logger.info(f"Batch {KEYGEN_STATE['batch_id']} completed")

            # Advance to next batch
            KEYGEN_STATE["batch_id"] += 1
            KEYGEN_STATE["index_within_batch"] = 0
            set_metric("vanity_progress_percent", 0)
            # Record start of next batch so restarts begin at correct position
            save_checkpoint(
                {
                    "batch_id": KEYGEN_STATE["batch_id"],
                    "index_within_batch": 0,
                }
            )

    except KeyboardInterrupt:
        logger.info("🛑 Keygen loop interrupted by user. Exiting cleanly.")
    except Exception:
        logger.exception("❌ Unexpected error in keygen loop")
    finally:
        set_metric("status.keygen", "Stopped")
        try:
            from core.dashboard import set_thread_health

            set_thread_health("keygen", False)
        except Exception:
            logger.warning("Failed to update keygen thread health", exc_info=True)


# ---------------------------------------------------------------------------
# One-shot debug entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🧪 Running one-shot VanitySearch test with random seed...")
    test_seed = generate_random_seed()
    run_vanitysearch_stream(test_seed, 999, 0, None)
