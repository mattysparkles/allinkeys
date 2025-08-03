import os
import json
import time
from datetime import datetime
from config.settings import (
    CHECKPOINT_PATH, CHECKPOINT_ENABLED, CHECKPOINT_INTERVAL_SECONDS,
    MAX_CHECKPOINT_HISTORY
)
from core.logger import get_logger

# Dedicated module logger
logger = get_logger(__name__)

CHECKPOINT_HISTORY_DIR = os.path.dirname(CHECKPOINT_PATH)


def save_keygen_checkpoint(state: dict):
    """Persist the current keygen progress to disk.

    A timestamped snapshot is written to ``CHECKPOINT_HISTORY_DIR`` and the
    latest state is stored at ``CHECKPOINT_PATH``.  This allows the key
    generation process to resume after interruptions.
    """
    if not CHECKPOINT_ENABLED or not isinstance(state, dict):
        logger.debug("Checkpoint save skipped – disabled or invalid state")
        return

    try:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        checkpoint_file = f"checkpoint_{timestamp}.json"
        checkpoint_path = os.path.join(CHECKPOINT_HISTORY_DIR, checkpoint_file)
        logger.debug(f"Saving checkpoint snapshot to {checkpoint_path}")

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
        logger.info(f"Checkpoint saved → {checkpoint_file}")

    except Exception:
        logger.exception("Failed to save checkpoint")


def load_keygen_checkpoint():
    """Return the latest saved keygen state if present."""
    if not CHECKPOINT_ENABLED:
        logger.debug("Checkpoint load skipped – feature disabled")
        return {}

    try:
        if os.path.exists(CHECKPOINT_PATH):
            logger.debug(f"Loading checkpoint from {CHECKPOINT_PATH}")
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Checkpoint loaded successfully")
            return data
        logger.debug("No checkpoint file found; starting fresh")
        return {}

    except Exception:
        logger.exception("Failed to load checkpoint")
        return {}


def _prune_old_checkpoints():
    """Delete stale checkpoint snapshots beyond ``MAX_CHECKPOINT_HISTORY``."""
    try:
        all_files = sorted(
            [f for f in os.listdir(CHECKPOINT_HISTORY_DIR) if f.startswith("checkpoint_")],
            reverse=True,
        )
        for f in all_files[MAX_CHECKPOINT_HISTORY:]:
            try:
                os.remove(os.path.join(CHECKPOINT_HISTORY_DIR, f))
                logger.debug(f"Pruned old checkpoint {f}")
            except Exception:
                logger.exception(f"Failed to delete old checkpoint {f}")

    except Exception:
        logger.exception("Prune failed")


def checkpoint_loop(state_fn=None):
    """Periodically invoke ``save_keygen_checkpoint`` using ``state_fn``."""
    if not CHECKPOINT_ENABLED:
        logger.debug("Checkpoint loop skipped – feature disabled")
        return

    from core.keygen import keygen_progress  # prevent circular import

    while True:
        time.sleep(CHECKPOINT_INTERVAL_SECONDS)
        try:
            data = state_fn() if state_fn else keygen_progress()
            if isinstance(data, dict):
                logger.debug("Checkpoint loop obtained state")
                save_keygen_checkpoint(data)
            else:
                logger.warning("Checkpoint data was not a dict; skipping save")
        except Exception:
            logger.exception("Checkpoint loop error")


def save_csv_checkpoint(batch_id: int, csv_path: str):
    """Append ``csv_path`` to a log tracking processed CSV batches."""
    try:
        checkpoint_log = CHECKPOINT_PATH.replace(".json", "_csv.log")
        logger.debug(f"Recording CSV checkpoint {batch_id} → {csv_path}")
        with open(checkpoint_log, "a", encoding="utf-8") as f:
            f.write(f"csv:{batch_id}:{csv_path}\n")
    except Exception:
        logger.exception(f"Failed to save CSV checkpoint for batch {batch_id}")
