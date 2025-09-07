# core/keygen.py

import os
import sys
import time
import subprocess
import threading
import logging
import secrets
import platform
from datetime import datetime
from collections import deque
from config.settings import (
    VANITY_OUTPUT_DIR,
    VANITY_PATTERN,
    BATCH_SIZE,
    ADDR_PER_FILE,
    LOGGING_ENABLED,
    CHECKPOINT_PATH,
    MAX_OUTPUT_FILE_SIZE,
    MAX_OUTPUT_LINES,
    ROTATE_INTERVAL_SECONDS,
    FILES_PER_BATCH,
    find_vanitysearch_binary,
)

from config.constants import SECP256K1_ORDER
from core.checkpoint import load_keygen_checkpoint as load_checkpoint, save_keygen_checkpoint as save_checkpoint
from core.gpu_selector import get_vanitysearch_gpu_ids  # ✅ Correct GPU selection integration
from core.logger import get_logger
import config.settings as settings
from core.seed_tracker import (
    seed_in_used_range,
    record_seed_range,
    get_condensed_ranges,
)
from core.utils.process import popen_with_retry


# Runtime trackers
total_keys_generated = 0
keygen_start_time = time.time()
last_output_file = None
KPS_WINDOW = deque()

# Prefetch queue to reduce I/O when checking used seeds
_SEED_QUEUE: list[int] = []
SEED_QUEUE_SIZE = 10

# Used to track current batch progress
KEYGEN_STATE = {
    "batch_id": 0,
    "index_within_batch": 0,
    "last_seed": None
}

# Setup centralized logging
logger = get_logger("keygen")


# ---------------------------------------------------------------------------
# BTC-only key generator
# ---------------------------------------------------------------------------

# Tracks whether BTC addresses should be generated in compressed form.
BTC_COMPRESSED = True


def run_btc_only(
    compressed: bool,
    shared_metrics=None,
    shutdown_event=None,
    pause_event=None,
    gpu_flag=None,
) -> int:
    """Run VanitySearch in BTC-only mode.

    This launches the standard key generation loop used by the main
    application but restricts execution to VanitySearch only.  The call blocks
    until the loop exits (for example via ``Ctrl+C``).

    Args:
        compressed: ``True`` to generate compressed addresses, ``False`` for
            uncompressed addresses.

    Returns
    -------
    int
        ``0`` when the loop terminates, ``1`` if an unexpected error occurs.
    """
    global BTC_COMPRESSED
    BTC_COMPRESSED = bool(compressed)

    mode = "compressed" if BTC_COMPRESSED else "uncompressed"
    logger.info(f"🔑 BTC-only keygen starting (format={mode})")

    try:
        # Re‑use the standard generator loop so behaviour matches the
        # full application, but avoid spawning extra processes.  Forward
        # any supplied shared ``multiprocessing`` primitives so the GUI can
        # control and observe the loop just like in the full application.
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
    elapsed_seconds = max(1, int(time.time() - keygen_start_time))
    elapsed_time_str = str(datetime.utcfromtimestamp(elapsed_seconds).strftime('%H:%M:%S'))
    if len(KPS_WINDOW) >= 2:
        keys_per_sec = (KPS_WINDOW[-1][1] - KPS_WINDOW[0][1]) / max(1e-6, KPS_WINDOW[-1][0] - KPS_WINDOW[0][0])
    else:
        keys_per_sec = 0
    return {
        "total_keys_generated": total_keys_generated,
        "current_batch_id": KEYGEN_STATE["batch_id"],
        "index_within_batch": KEYGEN_STATE["index_within_batch"],
        "last_seed": KEYGEN_STATE["last_seed"],
        "elapsed_time": elapsed_time_str,
        "start_timestamp": datetime.utcfromtimestamp(keygen_start_time).isoformat() + "Z",
        "keys_per_sec": round(keys_per_sec, 2),
    }


def generate_seed_from_batch(batch_id, index_within_batch, batch_size=1024000):
    """Derive a deterministic seed from ``batch_id`` and ``index``.

    This ensures each output file in a batch has a unique starting seed while
    still being reproducible across runs.
    """
    seed = batch_id * batch_size + index_within_batch
    min_val = 1 << 128
    if seed < min_val:
        seed += min_val
    if seed >= SECP256K1_ORDER:
        return None
    return seed


def generate_random_seed(min_bits=128):
    """Generate the next seed for VanitySearch while avoiding used ranges."""

    global _SEED_QUEUE

    while True:
        if getattr(settings, "PUZZLE_MODE", False):
            puzzle_num = getattr(settings, "PUZZLE_NUMBER", None)
            if puzzle_num is not None:
                from core import puzzle_queue as pq  # depends on SQLite
                chunk_idx = getattr(settings, "PUZZLE_CHUNK_INDEX", None)

                # ``os.uname`` is unavailable on some platforms (e.g., Windows).
                # ``platform.node`` provides a portable host identifier which
                # replicates the ``nodename`` attribute used previously.
                host_id = platform.node() if hasattr(platform, "node") else "unknown"
                seed = pq.next_seed(puzzle_num, host_id, chunk_idx)
                if seed is None:
                    raise RuntimeError(
                        f"No remaining puzzle-{puzzle_num} chunks to process"
                    )
                if seed_in_used_range(seed):
                    continue
                return seed
            # Fallback to random within start/end if puzzle number is missing
            start = int(getattr(settings, "PUZZLE_START", "0"), 16)
            end = int(getattr(settings, "PUZZLE_END", "0"), 16)
            if end < start:
                start, end = end, start
            span = max(1, end - start + 1)
            seed = secrets.randbelow(span) + start
            if seed_in_used_range(seed):
                continue
            return seed

        if _SEED_QUEUE:
            return _SEED_QUEUE.pop(0)

        min_val = 1 << min_bits
        range_span = SECP256K1_ORDER - min_val
        ranges = get_condensed_ranges()
        candidates = [secrets.randbelow(range_span) + min_val for _ in range(SEED_QUEUE_SIZE)]
        if any(seed_in_used_range(c, ranges) for c in candidates):
            continue
        _SEED_QUEUE.extend(candidates)


def run_vanitysearch_stream(initial_seed_int, batch_id, index_within_batch, pause_event=None, gpu_flag=None):
    """Run VanitySearch once and return when the output file is rotated.

    Returns ``True`` if the file was generated successfully, ``False`` if the
    process was interrupted (e.g. via the pause button).
    """
    global total_keys_generated, last_output_file

    # ``gpu_flag`` allows the GUI to toggle GPU usage at runtime. When False we
    # skip setting ``CUDA_VISIBLE_DEVICES`` so VanitySearch runs on CPU only.
    use_gpu = True if gpu_flag is None else bool(gpu_flag.value)
    selected_gpu_ids = get_vanitysearch_gpu_ids() if use_gpu else []
    gpu_env = {"CUDA_VISIBLE_DEVICES": ",".join(str(i) for i in selected_gpu_ids)} if selected_gpu_ids else {}

    hex_seed_full = hex(initial_seed_int)[2:].rjust(64, "0")
    hex_seed_short = hex(initial_seed_int)[2:].lstrip("0")[:8] or "00000000"

    current_output_path = os.path.join(
        VANITY_OUTPUT_DIR,
        f"batch_{batch_id}_part_{index_within_batch}_seed_{hex_seed_short}.txt"
    )
    last_output_file = current_output_path

    exe_path = find_vanitysearch_binary()
    if not exe_path:
        logger.error("VanitySearch binary not found.")
        raise FileNotFoundError("VanitySearch binary not found.")
    cmd = [exe_path, "-s", hex_seed_full, "-o", current_output_path]
    if use_gpu:
        cmd.append("-gpu")  # Enable CUDA acceleration
    if not BTC_COMPRESSED:
        cmd.append("-u")
    cmd.append(VANITY_PATTERN)
    logger.info(
        f"🧬 Starting VanitySearch:\n   Seed: {hex_seed_full}\n   Output: {current_output_path}\n   GPUs: {selected_gpu_ids or 'CPU'}"
    )
    logger.info(f"🚀 Running command: {' '.join(cmd)}")
    if pause_event and pause_event.is_set():
        logger.info("⏸️ Pause detected before launch. Skipping VanitySearch run.")
        return False

    lines = 0
    first_seed = None
    last_seed_local = None

    try:
        with open(current_output_path, "w", encoding="utf-8", buffering=1) as outfile:
            logger.info(f"Opened {current_output_path} for writing")
            # Launch VanitySearch as a subprocess and stream output to the file
            proc = popen_with_retry(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, **gpu_env},
                text=True,
                bufsize=1,
                module="keygen",
            )
            logger.info(f"Spawned VanitySearch PID {proc.pid} with args {cmd}")

            def monitor_process(p, path):
                """Monitor file size and pause requests while VanitySearch runs."""
                start = time.time()
                while p.poll() is None:
                    if pause_event and pause_event.is_set():
                        logger.info("⏸️ Pause requested. Terminating VanitySearch process...")
                        p.terminate()
                        break
                    if time.time() - start >= ROTATE_INTERVAL_SECONDS:
                        logger.info("⏱️ Rotation interval reached. Terminating process to rotate file.")
                        p.terminate()
                        break
                    try:
                        if os.path.getsize(path) >= MAX_OUTPUT_FILE_SIZE:
                            logger.info(
                                f"📏 Max file size reached ({MAX_OUTPUT_FILE_SIZE} bytes). Rotating file {os.path.basename(path)}"
                            )
                            p.terminate()
                            break
                    except FileNotFoundError:
                        logger.debug("Output file not yet created during monitoring")
                    time.sleep(1)

            timer_thread = threading.Thread(target=monitor_process, args=(proc, current_output_path))
            timer_thread.start()

            for raw_line in proc.stdout:
                outfile.write(raw_line)
                if raw_line.startswith("Priv (HEX):"):
                    hex_val = raw_line.split(":", 1)[1].strip().replace("0x", "")
                    seed_int = int(hex_val, 16)
                    if first_seed is None:
                        first_seed = seed_int
                    last_seed_local = seed_int
                    lines += 1
                    if lines >= MAX_OUTPUT_LINES:
                        logger.info(
                            f"📏 Max line count reached ({MAX_OUTPUT_LINES} lines). Rotating file {os.path.basename(current_output_path)}"
                        )
                        proc.terminate()
                        break

            proc.stdout.close()
            proc.wait()
            timer_thread.join()
    except Exception as e:
        logger.exception(f"Failed to execute VanitySearch: {e}")
        return False

    if lines == 0:
        logger.warning(f"⚠️ Output file empty: {current_output_path}")
        try:
            os.remove(current_output_path)
        except FileNotFoundError:
            pass
        return False

    total_keys_generated += lines
    increment_metric("keys_generated_today", lines)
    increment_metric("keys_generated_lifetime", lines)
    from core.dashboard import update_dashboard_stat, get_metric
    update_dashboard_stat("keys_generated_today", get_metric("keys_generated_today"))
    update_dashboard_stat("keys_generated_lifetime", get_metric("keys_generated_lifetime"))
    if first_seed is not None and last_seed_local is not None:
        record_seed_range(first_seed, last_seed_local)
    logger.info(f"📄 File complete: {lines} lines → {current_output_path}")
    return True



from core.dashboard import init_shared_metrics, set_metric, increment_metric, get_metric


def start_keygen_loop(shared_metrics=None, shutdown_event=None, pause_event=None, gpu_flag=None):
    try:
        init_shared_metrics(shared_metrics)
        logger.debug(f"Shared metrics initialized for {__name__}")
    except Exception as e:
        logger.exception(f"init_shared_metrics failed in {__name__}: {e}")
    from core.dashboard import register_control_events
    register_control_events(shutdown_event, pause_event, module="keygen")
    if not os.path.exists(VANITY_OUTPUT_DIR):
        os.makedirs(VANITY_OUTPUT_DIR)
    if getattr(settings, "PUZZLE_MODE", False) and getattr(settings, "PUZZLE_NUMBER", None) is not None:
        # Prepare SQLite queue with deterministic ranges
        from core import puzzle_queue as pq
        pq.init_work_queue()
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
                # Emit a heartbeat log every 5s while paused so the user knows
                # the key generator is still alive.
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
                kps = 0
            set_metric("keys_per_sec", round(kps, 2))

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

                success = run_vanitysearch_stream(seed, KEYGEN_STATE["batch_id"], index, pause_evt, gpu_flag)
                if not success:
                    time.sleep(1)
                    continue

                # Save after each file so progress can resume mid-batch
                save_checkpoint({
                    "batch_id": KEYGEN_STATE["batch_id"],
                    "index_within_batch": index + 1,
                })
                index += 1

            batch_end = time.perf_counter()
            batches_completed += 1
            total_time += batch_end - batch_start
            set_metric("batches_completed", batches_completed)
            set_metric("avg_keygen_time", round(total_time / batches_completed, 2))
            logger.info(f"Batch {KEYGEN_STATE['batch_id']} completed")

            KEYGEN_STATE["batch_id"] += 1
            KEYGEN_STATE["index_within_batch"] = 0
            set_metric("vanity_progress_percent", 0)
            # Record start of next batch so restarts begin at correct position
            save_checkpoint({
                "batch_id": KEYGEN_STATE["batch_id"],
                "index_within_batch": 0,
            })

    except KeyboardInterrupt:
        logger.info("🛑 Keygen loop interrupted by user. Exiting cleanly.")
    except Exception as e:
        # Log full stack trace for any unexpected failure
        logger.exception("❌ Unexpected error in keygen loop")
    finally:
        set_metric("status.keygen", "Stopped")
        try:
            from core.dashboard import set_thread_health
            set_thread_health("keygen", False)
        except Exception:
            logger.warning("Failed to update keygen thread health", exc_info=True)


# 🧪 One-time run (for debugging only)
if __name__ == "__main__":
    print("🧪 Running one-shot VanitySearch test with random seed...")
    test_seed = generate_random_seed()
    run_vanitysearch_stream(test_seed, 999, 0, None)
