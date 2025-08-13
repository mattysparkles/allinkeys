# core/keygen.py

import os
import time
import logging
import secrets
from datetime import datetime
from collections import deque
from config.settings import (
    VANITY_OUTPUT_DIR,
    VANITY_PATTERN,
    BATCH_SIZE,
    ADDR_PER_FILE,
    CHECKPOINT_PATH,
    MAX_OUTPUT_LINES,
    ROTATE_INTERVAL_SECONDS,
    FILES_PER_BATCH,
    FORCE_CPU_FALLBACK

)

from config.constants import SECP256K1_ORDER
from core.checkpoint import load_keygen_checkpoint as load_checkpoint, save_keygen_checkpoint as save_checkpoint
from core.logger import get_logger
from core import vanity_runner


# Runtime trackers
total_keys_generated = 0
keygen_start_time = time.time()
last_output_file = None
KPS_WINDOW = deque()

# Used to track current batch progress
KEYGEN_STATE = {
    "batch_id": 0,
    "index_within_batch": 0,
    "last_seed": None
}

# Setup centralized logging
logger = get_logger("keygen")


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
    """Generate a cryptographically secure random seed."""
    min_val = 1 << min_bits
    range_span = SECP256K1_ORDER - min_val
    return secrets.randbelow(range_span) + min_val


def run_vanitysearch_stream(initial_seed_int, batch_id, index_within_batch, pause_event=None, gpu_flag=None):
    """Run VanitySearch once and return when the output file is rotated.

    Returns ``True`` if the file was generated successfully, ``False`` if the
    process was interrupted (e.g. via the pause button).
    """
    global total_keys_generated, last_output_file

    hex_seed_full = hex(initial_seed_int)[2:].rjust(64, "0")
    hex_seed_short = hex(initial_seed_int)[2:].lstrip("0")[:8] or "00000000"

    current_output_path = os.path.join(
        VANITY_OUTPUT_DIR,
        f"batch_{batch_id}_part_{index_within_batch}_seed_{hex_seed_short}.txt"
    )
    last_output_file = current_output_path

    seed_args = ["-s", hex_seed_full, "-u", VANITY_PATTERN]
    backend = vanity_runner.get_selected_backend()
    device_id = vanity_runner.get_selected_device_id()
    success = vanity_runner.run_vanitysearch(
        seed_args,
        current_output_path,
        device_id,
        backend,
        timeout=ROTATE_INTERVAL_SECONDS,
        pause_event=pause_event,
    )
    if not success:
        return False

    if os.path.exists(current_output_path):
        size = os.path.getsize(current_output_path)
        if size == 0:
            logger.warning(f"⚠️ Output file empty: {current_output_path}")
            os.remove(current_output_path)
            return False
        try:
            with open(current_output_path, 'r', encoding='utf-8') as f:
                logger.info(f"Opened {current_output_path} for reading")
                lines = sum(1 for _ in f)
                total_keys_generated += lines
                increment_metric("keys_generated_today", lines)
                increment_metric("keys_generated_lifetime", lines)
                from core.dashboard import update_dashboard_stat, get_metric
                update_dashboard_stat("keys_generated_today", get_metric("keys_generated_today"))
                update_dashboard_stat("keys_generated_lifetime", get_metric("keys_generated_lifetime"))
                logger.info(f"📄 File complete: {lines} lines → {current_output_path}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to count lines in {current_output_path}: {e}")
        return True
    else:
        logger.error(f"❌ Output file not created: {current_output_path}")
        return False



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

    backend, device_id, device_name, binary = vanity_runner.probe_device()
    logger.info(
        f"Startup selection → device: {device_name} | backend: {backend} | binary: {binary} | FORCE_CPU_FALLBACK={FORCE_CPU_FALLBACK}"
    )

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
