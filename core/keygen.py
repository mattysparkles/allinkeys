# core/keygen.py

import os
import time
import subprocess
import threading
import secrets
import platform
from datetime import datetime
from collections import deque

from config.settings import (
    VANITYSEARCH_PATH,
    VANITY_OUTPUT_DIR,
    VANITY_PATTERN,
    MAX_OUTPUT_FILE_SIZE,
    MAX_OUTPUT_LINES,
    ROTATE_INTERVAL_SECONDS,
    FILES_PER_BATCH,
)
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
from core.seed_tracker import seed_in_used_range, record_seed_range

# Runtime trackers / metrics window
total_keys_generated = 0
keygen_start_time = time.time()
last_output_file = None
KPS_WINDOW = deque()

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


def generate_random_seed(min_bits=128):
    """
    Generate the next seed for VanitySearch while avoiding used ranges.
    Honors PUZZLE_MODE and related settings when enabled.
    Always returns an int.
    """
    while True:
        # Puzzle mode: pull deterministic chunks from SQLite queue
        if getattr(settings, "PUZZLE_MODE", False):
            puzzle_num = getattr(settings, "PUZZLE_NUMBER", None)
            if puzzle_num is not None:
                from core import puzzle_queue as pq  # depends on SQLite

                chunk_idx = getattr(settings, "PUZZLE_CHUNK_INDEX", None)
                # Portable host identifier (works on Windows too)
                host_id = platform.node() if hasattr(platform, "node") else "unknown"
                seed = pq.next_seed(puzzle_num, host_id, chunk_idx)
                if seed is None:
                    raise RuntimeError(
                        f"No remaining puzzle-{puzzle_num} chunks to process"
                    )
                if seed_in_used_range(seed):
                    continue
                return seed

            # Fallback: bounded random between PUZZLE_START and PUZZLE_END
            start = int(getattr(settings, "PUZZLE_START", "0"), 16)
            end = int(getattr(settings, "PUZZLE_END", "0"), 16)
            if end < start:
                start, end = end, start
            span = max(1, end - start + 1)
            seed = secrets.randbelow(span) + start
            if seed_in_used_range(seed):
                continue
            return seed

        # Standard random seed within [2^min_bits, SECP256K1_ORDER)
        min_val = 1 << min_bits
        range_span = SECP256K1_ORDER - min_val
        seed = secrets.randbelow(range_span) + min_val
        if seed_in_used_range(seed):
            continue
        return seed


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
    last_output_file = current_output_path

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

    cmd = [exe_path, "-s", str(hex_seed_full), "-o", str(current_output_path)]
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
            """
            Periodically check for pause/rotation triggers while VanitySearch runs:
            - rotate by elapsed time (ROTATE_INTERVAL_SECONDS)
            - rotate by size (MAX_OUTPUT_FILE_SIZE)
            - rotate by lines (MAX_OUTPUT_LINES)
            """
            start = time.time()
            while p.poll() is None:
                if pause_event and pause_event.is_set():
                    logger.info(
                        "⏸️ Pause requested. Terminating VanitySearch process..."
                    )
                    p.terminate()
                    break
                if time.time() - start >= ROTATE_INTERVAL_SECONDS:
                    logger.info(
                        "⏱️ Rotation interval reached. Terminating process to rotate file."
                    )
                    p.terminate()
                    break
                try:
                    if os.path.exists(path):
                        # Size-based rotation
                        if os.path.getsize(path) >= MAX_OUTPUT_FILE_SIZE:
                            logger.info(
                                f"📏 Max file size reached ({MAX_OUTPUT_FILE_SIZE} bytes). "
                                f"Rotating file {os.path.basename(path)}"
                            )
                            p.terminate()
                            break
                        # Line-count rotation
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            if sum(1 for _ in f) >= MAX_OUTPUT_LINES:
                                logger.info(
                                    f"📏 Max line count reached ({MAX_OUTPUT_LINES} lines). "
                                    f"Rotating file {os.path.basename(path)}"
                                )
                                p.terminate()
                                break
                except FileNotFoundError:
                    logger.debug("Output file not yet created during monitoring")
                time.sleep(1)

        timer_thread = threading.Thread(
            target=monitor_process, args=(proc, current_output_path)
        )
        timer_thread.start()
        proc.wait()
        timer_thread.join()
    except Exception as e:
        logger.exception(f"Failed to execute VanitySearch: {e}")
        return False

    # Post-process output file: delete only if truly empty; update metrics
    if os.path.exists(current_output_path):
        size = os.path.getsize(current_output_path)
        if size == 0:
            logger.warning(f"⚠️ Output file empty: {current_output_path}")
            os.remove(current_output_path)
            return False

        try:
            with open(current_output_path, "r", encoding="utf-8") as f:
                logger.info(f"Opened {current_output_path} for reading")
                lines = 0
                first_seed = None
                last_seed = None
                for line in f:
                    # Keep existing marker; widen here if needed to also accept "Privkey:"
                    if line.startswith("Priv (HEX):"):
                        hex_val = line.split(":", 1)[1].strip().replace("0x", "")
                        seed_int = int(hex_val, 16)
                        if first_seed is None:
                            first_seed = seed_int
                        last_seed = seed_int
                        lines += 1

            # Update counters/metrics and seed ranges
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

            logger.info(f"📄 File complete: {lines} lines → {current_output_path}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to count lines in {current_output_path}: {e}")
        return True

    logger.error(f"❌ Output file not created: {current_output_path}")
    return False


# Dashboard metric helpers (imported here to keep module import order)
from core.dashboard import (
    init_shared_metrics,
    set_metric,
    increment_metric,
    get_metric,
)  # noqa: E402


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
    if not os.path.exists(VANITY_OUTPUT_DIR):
        os.makedirs(VANITY_OUTPUT_DIR, exist_ok=True)

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

                success = run_vanitysearch_stream(
                    seed, KEYGEN_STATE["batch_id"], index, pause_evt, gpu_flag
                )
                if not success:
                    time.sleep(1)
                    continue

                # Save after each file so progress can resume mid-batch
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
