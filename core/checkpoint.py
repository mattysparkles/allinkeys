import os
import json
import time
from datetime import datetime
from config.settings import (
    CHECKPOINT_PATH, CHECKPOINT_ENABLED, CHECKPOINT_INTERVAL_SECONDS,
    MAX_CHECKPOINT_HISTORY
)
from core.logger import log_message

CHECKPOINT_HISTORY_DIR = os.path.dirname(CHECKPOINT_PATH)


def save_keygen_checkpoint(state: dict):
    """
    Save the checkpoint data to a JSON file, with a timestamped backup.
    Includes full runtime progress: batch_id, index, seed, output_file, etc.
    """
    if not CHECKPOINT_ENABLED or not isinstance(state, dict):
        return

    try:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        checkpoint_file = f"checkpoint_{timestamp}.json"
        checkpoint_path = os.path.join(CHECKPOINT_HISTORY_DIR, checkpoint_file)

        # Add UTC timestamp to saved object
        state["timestamp"] = datetime.utcnow().isoformat() + "Z"

        # Save versioned backup
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)

        # Prune old snapshots
        _prune_old_checkpoints()

        # Overwrite main checkpoint file
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)

        log_message(f"💾 Checkpoint saved: {checkpoint_file}", "DEBUG")

    except Exception as e:
        log_message(f"❌ Failed to save checkpoint: {e}", "ERROR")


def load_keygen_checkpoint():
    """
    Load the most recent keygen checkpoint from disk.
    Returns dictionary with: batch_id, index_within_batch, last_seed, output_file, etc.
    """
    if not CHECKPOINT_ENABLED:
        return {}

    try:
        if os.path.exists(CHECKPOINT_PATH):
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            log_message("✅ Checkpoint loaded successfully", "INFO")
            return data
        else:
            log_message("🧼 No checkpoint found, starting fresh", "DEBUG")
            return {}

    except Exception as e:
        log_message(f"❌ Failed to load checkpoint: {e}", "ERROR")
        return {}


def _prune_old_checkpoints():
    """
    Remove old checkpoint backup files beyond MAX_CHECKPOINT_HISTORY.
    """
    try:
        all_files = sorted(
            [f for f in os.listdir(CHECKPOINT_HISTORY_DIR) if f.startswith("checkpoint_")],
            reverse=True
        )
        for f in all_files[MAX_CHECKPOINT_HISTORY:]:
            try:
                os.remove(os.path.join(CHECKPOINT_HISTORY_DIR, f))
                log_message(f"🧹 Pruned old checkpoint: {f}", "DEBUG")
            except Exception as e:
                log_message(f"❌ Failed to delete old checkpoint {f}: {e}", "ERROR")

    except Exception as e:
        log_message(f"❌ Prune failed: {e}", "ERROR")


def checkpoint_loop(state_fn=None):
    """
    Background thread loop to save keygen checkpoints every X seconds.
    Accepts custom state provider function, defaults to keygen_progress().
    """
    if not CHECKPOINT_ENABLED:
        return

    from core.keygen import keygen_progress  # prevent circular import

    while True:
        time.sleep(CHECKPOINT_INTERVAL_SECONDS)
        try:
            data = state_fn() if state_fn else keygen_progress()
            if isinstance(data, dict):
                save_keygen_checkpoint(data)
            else:
                log_message("⚠️ Checkpoint data was not a dict. Skipped save.", "WARNING")
        except Exception as e:
            log_message(f"❌ Checkpoint loop error: {e}", "ERROR")


def save_csv_checkpoint(batch_id: int, csv_path: str):
    """
    Records CSV output batch checkpoint to avoid duplicate processing.
    Logs each line to separate .log file.
    """
    try:
        checkpoint_log = CHECKPOINT_PATH.replace(".json", "_csv.log")
        with open(checkpoint_log, "a", encoding="utf-8") as f:
            f.write(f"csv:{batch_id}:{csv_path}\n")
    except Exception as e:
        log_message(f"❌ Failed to save CSV checkpoint for batch {batch_id}: {e}", "ERROR")
