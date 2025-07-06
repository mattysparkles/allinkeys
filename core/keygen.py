# core/keygen.py

import os
import sys
import time
import subprocess
import threading
import logging
import secrets
from datetime import datetime
from config.settings import (
    VANITYSEARCH_PATH,
    VANITY_OUTPUT_DIR,
    VANITY_PATTERN,
    BATCH_SIZE,
    ADDR_PER_FILE,
    LOGGING_ENABLED,
    CHECKPOINT_PATH,
    MAX_OUTPUT_FILE_SIZE,
    MAX_OUTPUT_LINES,
    ROTATE_INTERVAL_SECONDS,
    FILES_PER_BATCH

)

from config.constants import SECP256K1_ORDER
from core.checkpoint import load_keygen_checkpoint as load_checkpoint, save_keygen_checkpoint as save_checkpoint
from core.gpu_selector import get_vanitysearch_gpu_ids  # ✅ Correct GPU selection integration

# Runtime trackers
total_keys_generated = 0
keygen_start_time = time.time()
last_output_file = None

# Used to track current batch progress
KEYGEN_STATE = {
    "batch_id": 0,
    "index_within_batch": 0,
    "last_seed": None
}

# Setup logging
logger = logging.getLogger("KeyGen")
logger.setLevel(logging.INFO)
if LOGGING_ENABLED:
    handler = logging.FileHandler("keygen.log", encoding='utf-8')
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def keygen_progress():
    elapsed_seconds = max(1, int(time.time() - keygen_start_time))
    elapsed_time_str = str(datetime.utcfromtimestamp(elapsed_seconds).strftime('%H:%M:%S'))
    keys_per_sec = total_keys_generated / elapsed_seconds
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
    seed = batch_id * batch_size + index_within_batch
    min_val = 1 << 128
    if seed < min_val:
        seed += min_val
    if seed >= SECP256K1_ORDER:
        return None
    return seed


def generate_random_seed(min_bits=128):
    min_val = 1 << min_bits
    range_span = SECP256K1_ORDER - min_val
    return secrets.randbelow(range_span) + min_val


def run_vanitysearch_stream(initial_seed_int, batch_id, index_within_batch, pause_event=None):
    """Run VanitySearch once and return when the output file is rotated.

    Returns ``True`` if the file was generated successfully, ``False`` if the
    process was interrupted (e.g. via the pause button).
    """
    global total_keys_generated, last_output_file

    selected_gpu_ids = get_vanitysearch_gpu_ids()
    gpu_env = {"CUDA_VISIBLE_DEVICES": ",".join(str(i) for i in selected_gpu_ids)} if selected_gpu_ids else {}

    hex_seed_full = hex(initial_seed_int)[2:].rjust(64, "0")
    hex_seed_short = hex(initial_seed_int)[2:].lstrip("0")[:8] or "00000000"

    current_output_path = os.path.join(
        VANITY_OUTPUT_DIR,
        f"batch_{batch_id}_part_{index_within_batch}_seed_{hex_seed_short}.txt"
    )
    last_output_file = current_output_path

    cmd = [
        VANITYSEARCH_PATH,
        "-s", hex_seed_full,
        "-gpu",
        "-o", current_output_path,
        "-u", VANITY_PATTERN
    ]

    logger.info(
        f"🧬 Starting VanitySearch:\n   Seed: {hex_seed_full}\n   Output: {current_output_path}\n   GPUs: {selected_gpu_ids or 'default'}"
    )
    logger.info(f"🚀 Running command: {' '.join(cmd)}")
    if pause_event and pause_event.is_set():
        logger.info("⏸️ Pause detected before launch. Skipping VanitySearch run.")
        return False

    with open(current_output_path, "w", encoding="utf-8", buffering=1) as outfile:
        proc = subprocess.Popen(
            cmd,
            stdout=outfile,
            stderr=subprocess.STDOUT,
            env={**os.environ, **gpu_env}
        )

        def monitor_process(p, path):
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
                    pass
                time.sleep(1)

        timer_thread = threading.Thread(target=monitor_process, args=(proc, current_output_path))
        timer_thread.start()
        proc.wait()
        timer_thread.join()

    if os.path.exists(current_output_path):
        size = os.path.getsize(current_output_path)
        if size == 0:
            logger.warning(f"⚠️ Output file empty: {current_output_path}")
            os.remove(current_output_path)
            return False
        try:
            with open(current_output_path, 'r', encoding='utf-8') as f:
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


def start_keygen_loop(shared_metrics=None, shutdown_event=None, pause_event=None):
    try:
        init_shared_metrics(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)
    from core.dashboard import register_control_events
    register_control_events(shutdown_event, pause_event)
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

    try:
        set_metric("status.keygen", True)
        from core.dashboard import (
            set_thread_health,
            get_shutdown_event,
            get_pause_event,
        )
        set_thread_health("keygen", True)

        shutdown_evt = get_shutdown_event()
        pause_evt = get_pause_event()

        batches_completed = 0
        total_time = 0.0

        while True:
            if shutdown_evt and shutdown_evt.is_set():
                break
            batch_start = time.perf_counter()
            index = KEYGEN_STATE["index_within_batch"]
            while index < FILES_PER_BATCH:
                if shutdown_evt and shutdown_evt.is_set():
                    break
                if (
                    get_metric("global_run_state") == "paused"
                    or (pause_evt and pause_evt.is_set())
                ):
                    time.sleep(1)
                    continue

                seed = generate_seed_from_batch(KEYGEN_STATE["batch_id"], index)
                if seed is None:
                    index += 1
                    continue

                KEYGEN_STATE["index_within_batch"] = index
                KEYGEN_STATE["last_seed"] = hex(seed)[2:].rjust(64, "0")
                set_metric("current_seed_index", index)
                progress = round((index / float(FILES_PER_BATCH)) * 100, 2)
                set_metric("vanity_progress_percent", progress)

                success = run_vanitysearch_stream(seed, KEYGEN_STATE["batch_id"], index, pause_evt)
                if not success:
                    time.sleep(1)
                    continue

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

            KEYGEN_STATE["batch_id"] += 1
            KEYGEN_STATE["index_within_batch"] = 0
            set_metric("vanity_progress_percent", 0)
            save_checkpoint({
                "batch_id": KEYGEN_STATE["batch_id"],
                "index_within_batch": 0,
            })

    except KeyboardInterrupt:
        logger.info("🛑 Keygen loop interrupted by user. Exiting cleanly.")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
    finally:
        set_metric("status.keygen", False)
        try:
            from core.dashboard import set_thread_health
            set_thread_health("keygen", False)
        except Exception:
            pass


# 🧪 One-time run (for debugging only)
if __name__ == "__main__":
    print("🧪 Running one-shot VanitySearch test with random seed...")
    test_seed = generate_random_seed()
    run_vanitysearch_stream(test_seed, 999, 0, None)
