# core/keygen.py

import os
from pathlib import Path
import inspect
import time
import hashlib
import secrets
import platform
import re
from datetime import datetime
from collections import OrderedDict, deque
from threading import Lock

from config.settings import (
    VANITYSEARCH_PATH,
    VANITY_PATTERN,
    FILES_PER_BATCH,
)
from config.telemetry import TELEMETRY_CHECK_CACHE_TTL_SECONDS
from config.directories import VANITY_OUTPUT_DIR
from config.constants import SECP256K1_ORDER
from core.checkpoint import (
    load_keygen_checkpoint as load_checkpoint,
    save_keygen_checkpoint as save_checkpoint,
)
from core.gpu_selector import (
    get_vanitysearch_gpu_ids,
)  # ✅ Correct GPU selection integration
from core.logger import get_logger, log_with_context
import config.settings as settings
from core.vanity_runner import run_vanitysearch_batch
from core.vanity_tuning import apply_vanitysearch_tuning_args, supports_binary_flag
from core.seed_tracker import (
    seed_in_used_range,
    record_seed_range,
    get_condensed_ranges,
)
from core.seed_queue import dequeue as dequeue_seed_queue, size as seed_queue_size
from core.telemetry import (
    _get_app_id,
    check_seed_seen,
    record_range_event,
    record_seed_event,
)
from core.worker_bootstrap import _safe_set_metric

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
TELEMETRY_SEED_CACHE_MAX_SIZE = 10_000
telemetry_seed_cache: OrderedDict[str, tuple[bool, float]] = OrderedDict()
_TELEMETRY_SEED_CACHE_LOCK = Lock()

try:
    _RECORD_SEED_EVENT_SUPPORTS_RANGE_OBSERVATION = (
        "range_observation" in inspect.signature(record_seed_event).parameters
    )
except Exception:
    _RECORD_SEED_EVENT_SUPPORTS_RANGE_OBSERVATION = True


def _seed_in_ranges(seed: int, ranges) -> bool:
    """Return True if ``seed`` falls within any condensed ``ranges``."""

    for start, end in ranges:
        if start <= seed <= end:
            return True
    return False


def telemetry_enabled() -> bool:
    """Return ``True`` when experimental telemetry features are active."""

    if not bool(getattr(settings, "SEED_TELEMETRY_ENABLED", False)):
        return False
    try:
        from core.telemetry import telemetry_opted_out

        if telemetry_opted_out():
            return False
    except Exception:
        pass
    return True


def _emit_seed_event(
    seed: int,
    *,
    mode: str,
    range_id,
    used: bool,
    match_found: bool,
    range_observation=None,
) -> None:
    """Emit telemetry seed events with backward compatibility for older clients."""

    payload = int(seed).to_bytes(32, "big")
    kwargs = {
        "mode": mode,
        "range_id": range_id,
        "used": used,
        "match_found": match_found,
    }
    if (
        _RECORD_SEED_EVENT_SUPPORTS_RANGE_OBSERVATION
        and range_observation is not None
    ):
        kwargs["range_observation"] = range_observation
    try:
        record_seed_event(payload, **kwargs)
    except TypeError as exc:
        # Backward compatibility: some client builds may have a telemetry module
        # without the newer ``range_observation`` argument.
        if "range_observation" not in str(exc):
            raise
        kwargs.pop("range_observation", None)
        record_seed_event(payload, **kwargs)

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
    log_with_context(
        logger,
        "INFO",
        f"🔑 BTC-only keygen starting (format={mode})",
        **_keygen_log_context(mode="only_btc"),
    )

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
    try:
        from core.dashboard import get_metric

        active_mode = get_metric("active_mode")
        if isinstance(active_mode, str) and active_mode.strip():
            normalized = active_mode.strip().lower()
            if normalized == "only_btc":
                normalized = "btc_only"
            if normalized in {"btc_only", "vanity", "mnemonic"}:
                return normalized, "default"
    except Exception:
        pass
    return "vanity", "default"


def _keygen_log_context(
    *,
    batch_id=None,
    index_within_batch=None,
    gpu_ids=None,
    mode=None,
    range_id=None,
) -> dict:
    default_mode, default_range_id = _telemetry_context()
    return {
        "batch_id": batch_id,
        "index_within_batch": index_within_batch,
        "gpu_ids": gpu_ids,
        "mode": mode if mode is not None else default_mode,
        "range_id": range_id if range_id is not None else default_range_id,
    }


def _range_space() -> tuple[int, int]:
    """Return (space_min, space_max) for normalized range telemetry."""
    if getattr(settings, "PUZZLE_MODE", False):
        start = int(getattr(settings, "PUZZLE_START", "0"), 16)
        end = int(getattr(settings, "PUZZLE_END", "0"), 16)
        if end < start:
            start, end = end, start
        return start, end
    return 0, SECP256K1_ORDER - 1


def _format_range_id(start: int, end: int) -> str:
    """Return a stable, human-readable range identifier from bounds."""
    start_val, end_val = int(start), int(end)
    if end_val < start_val:
        start_val, end_val = end_val, start_val
    return f"0x{start_val:064x}-0x{end_val:064x}"


def _get_cached_seed_check(fingerprint: str, now: float) -> bool | None:
    with _TELEMETRY_SEED_CACHE_LOCK:
        entry = telemetry_seed_cache.get(fingerprint)
        if entry is None:
            return None
        used, expires_at = entry
        if now >= expires_at:
            telemetry_seed_cache.pop(fingerprint, None)
            return None
        telemetry_seed_cache.move_to_end(fingerprint)
        return used


def _set_cached_seed_check(fingerprint: str, used: bool, now: float) -> None:
    ttl_seconds = TELEMETRY_CHECK_CACHE_TTL_SECONDS
    if ttl_seconds <= 0:
        return
    expires_at = now + ttl_seconds
    with _TELEMETRY_SEED_CACHE_LOCK:
        telemetry_seed_cache[fingerprint] = (used, expires_at)
        telemetry_seed_cache.move_to_end(fingerprint)
        while len(telemetry_seed_cache) > TELEMETRY_SEED_CACHE_MAX_SIZE:
            telemetry_seed_cache.popitem(last=False)


def _central_seen(seed: int) -> bool:
    mode, range_id = _telemetry_context()
    if not telemetry_enabled():
        return False
    try:
        seed_bytes = int(seed).to_bytes(32, "big")
        fingerprint = hashlib.sha256(seed_bytes + _get_app_id().encode()).hexdigest()
        now = time.time()
        cached = _get_cached_seed_check(fingerprint, now)
        if cached is not None:
            try:
                increment_metric("telemetry_seed_cache_hits", 1)
            except Exception:
                pass
            return cached
        try:
            increment_metric("telemetry_seed_cache_misses", 1)
        except Exception:
            pass
        used = check_seed_seen(seed_bytes, mode=mode, range_id=range_id)
        _set_cached_seed_check(fingerprint, used, now)
        return used
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
                        _emit_seed_event(
                            candidate,
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
                        _emit_seed_event(
                            seed,
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
                    _emit_seed_event(
                        seed,
                        mode=_telemetry_context()[0],
                        range_id=_telemetry_context()[1],
                        used=True,
                        match_found=False,
                    )
                except Exception:
                    pass
                continue
            return seed

        queued_entry = dequeue_seed_queue()
        while queued_entry is not None:
            seed = int(queued_entry.seed_start)
            if seed < (1 << min_bits):
                queued_entry = dequeue_seed_queue()
                continue
            if seed_in_used_range(seed):
                queued_entry = dequeue_seed_queue()
                continue
            if telemetry_enabled() and _central_seen(seed):
                try:
                    _emit_seed_event(
                        seed,
                        mode=_telemetry_context()[0],
                        range_id=_telemetry_context()[1],
                        used=True,
                        match_found=False,
                    )
                except Exception:
                    pass
                queued_entry = dequeue_seed_queue()
                continue
            _safe_set_metric("seed_queue_depth", seed_queue_size())
            try:
                log_with_context(
                    logger,
                    "INFO",
                    "[Queue] Using queued seed %s | remaining=%s",
                    hex(seed),
                    seed_queue_size(),
                )
            except Exception:
                pass
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
        with open(
            path, "r", encoding="utf-8", errors="ignore", buffering=1024 * 1024
        ) as fh:
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
    initial_seed_int,
    batch_id,
    index_within_batch,
    pause_event=None,
    gpu_flag=None,
    shutdown_event=None,
):
    """
    Run one VanitySearch batch. A batch is one process invocation that writes
    exactly one output file via `-o <file>` and is bounded by settings limits.
    Rotation is implemented by process restart because VanitySearch cannot
    rotate files itself.
    Returns True if an output file exists at the end; False otherwise.
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
            log_with_context(
                logger,
                "DEBUG",
                "Seed %x outside puzzle range [%x, %x] — skipping",
                initial_seed_int,
                start,
                end,
                **_keygen_log_context(
                    batch_id=batch_id,
                    index_within_batch=index_within_batch,
                ),
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

    # Output path prefix (VanitySearch owns this file via -o). Each run MUST
    # generate a fresh file via -o. Do not reuse filenames or rotate in Python.
    output_prefix = (
        f"batch_{batch_id}_part_{index_within_batch}_seed_{hex_seed_short}"
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
        log_with_context(
            logger,
            "ERROR",
            "VanitySearch binary not found: %s",
            exe_path,
            **_keygen_log_context(
                batch_id=batch_id,
                index_within_batch=index_within_batch,
            ),
        )
        raise FileNotFoundError("VanitySearch binary not found.")

    # IMPORTANT: Use VanitySearch's native -o output handling with a unique
    # filename. Python must not manage file rotation or write output directly.
    base_args = ["-s", str(hex_seed_full)]
    if getattr(settings, "VANITY_CASE_INSENSITIVE", False):
        base_args.append("-i")
    if use_gpu:
        # VanitySearch builds vary: some accept `-gpu` only, others accept `-gpuId <id>`.
        base_args.append("-gpu")
        use_gpu_id_flag = getattr(settings, "VANITYSEARCH_GPU_ID_ARGUMENT", False)
        if not use_gpu_id_flag:
            use_gpu_id_flag = supports_binary_flag(
                exe_path, getattr(settings, "VANITYSEARCH_GPU_ID_FLAG", "gpuId")
            )
        if use_gpu_id_flag:
            gpu_id = selected_gpu_ids[0] if selected_gpu_ids else 0
            flag = getattr(settings, "VANITYSEARCH_GPU_ID_FLAG", "gpuId")
            base_args += [f"-{flag}", str(gpu_id)]
    if not BTC_COMPRESSED:
        base_args.append("-u")  # uncompressed WIF
    base_args = apply_vanitysearch_tuning_args(
        base_args,
        use_gpu=use_gpu,
        gpu_ids=selected_gpu_ids,
        backend="cuda" if use_gpu else "cpu",
        binary=exe_path,
    )

    cmd_preview = " ".join(map(str, [exe_path] + base_args + [pattern]))
    log_with_context(
        logger,
        "INFO",
        f"🧬 Starting VanitySearch:\n"
        f"   Seed: {hex_seed_full}\n"
        f"   Output prefix: {output_prefix}\n"
        f"   GPUs: {selected_gpu_ids or 'CPU'}\n"
        f"   Pattern: {pattern}",
        **_keygen_log_context(
            batch_id=batch_id,
            index_within_batch=index_within_batch,
            gpu_ids=selected_gpu_ids or None,
        ),
    )
    log_with_context(
        logger,
        "INFO",
        f"🚀 Running command: {cmd_preview}",
        **_keygen_log_context(
            batch_id=batch_id,
            index_within_batch=index_within_batch,
            gpu_ids=selected_gpu_ids or None,
        ),
    )

    # Respect pause before launch
    if (shutdown_event and shutdown_event.is_set()) or (
        pause_event and pause_event.is_set()
    ):
        log_with_context(
            logger,
            "INFO",
            "⏸️ Pause or shutdown detected before launch. Skipping VanitySearch run.",
            **_keygen_log_context(
                batch_id=batch_id,
                index_within_batch=index_within_batch,
                gpu_ids=selected_gpu_ids or None,
            ),
        )
        return False

    try:
        current_output_path, rc, rotation_info = run_vanitysearch_batch(
            binary=exe_path,
            base_args=base_args,
            output_dir=VANITY_OUTPUT_DIR,
            output_prefix=output_prefix,
            pattern=pattern,
            env={**os.environ, **gpu_env},
            pause_event=pause_event,
            shutdown_event=shutdown_event,
        )
        last_output_file = str(current_output_path)
        if rc != 0 and pause_event and pause_event.is_set():
            return False
        if shutdown_event and shutdown_event.is_set():
            return False
    except RuntimeError:
        logger.exception("VanitySearch execution aborted due to safeguard.")
        raise
    except Exception as e:
        logger.exception(f"Failed to execute VanitySearch: {e}")
        return False

    # Post-process output file: parse results and drop empty outputs. The file
    # itself must be created by VanitySearch via -o (no Python writes).
    if not os.path.exists(current_output_path):
        log_with_context(
            logger,
            "ERROR",
            f"❌ Output file not created: {current_output_path}",
            **_keygen_log_context(
                batch_id=batch_id,
                index_within_batch=index_within_batch,
                gpu_ids=selected_gpu_ids or None,
            ),
        )
        # Returning False forces the caller to retry this part instead of
        # silently advancing the batch counter without producing a file.
        return False

    if os.path.exists(current_output_path):
        size = os.path.getsize(current_output_path)
        if size == 0:
            log_with_context(
                logger,
                "WARNING",
                f"⚠️ Output file empty: {current_output_path}",
                **_keygen_log_context(
                    batch_id=batch_id,
                    index_within_batch=index_within_batch,
                    gpu_ids=selected_gpu_ids or None,
                ),
            )
            os.remove(current_output_path)
            return True

        try:
            lines, first_seed, last_seed = parse_vanity_file(current_output_path)

            from core.dashboard import update_dashboard_stat, get_metric

            total_keys_generated += lines
            increment_metric("keys_generated_today", lines)
            increment_metric("keys_generated_lifetime", lines)
            increment_metric("addresses_generated_today.btc", lines)
            increment_metric("addresses_generated_lifetime.btc", lines)
            update_dashboard_stat(
                "keys_generated_today", get_metric("keys_generated_today")
            )
            update_dashboard_stat(
                "keys_generated_lifetime", get_metric("keys_generated_lifetime")
            )
            update_dashboard_stat(
                "addresses_generated_today",
                get_metric("addresses_generated_today"),
            )
            update_dashboard_stat(
                "addresses_generated_lifetime",
                get_metric("addresses_generated_lifetime"),
            )
            range_id_override = None
            range_observation = None
            if first_seed is not None and last_seed is not None:
                record_seed_range(first_seed, last_seed)
                range_id_override = _format_range_id(first_seed, last_seed)
                if telemetry_enabled():
                    try:
                        mode, _ = _telemetry_context()
                        space_min, space_max = _range_space()
                        range_observation = record_range_event(
                            mode=mode,
                            range_id=range_id_override,
                            start=first_seed,
                            end=last_seed,
                            space_min=space_min,
                            space_max=space_max,
                        )
                    except Exception:
                        pass
            if telemetry_enabled() and range_observation is None:
                try:
                    mode, _ = _telemetry_context()
                    space_min, space_max = _range_space()
                    fallback_seed = int(initial_seed_int)
                    range_id_override = range_id_override or _format_range_id(
                        fallback_seed, fallback_seed
                    )
                    range_observation = record_range_event(
                        mode=mode,
                        range_id=range_id_override,
                        start=fallback_seed,
                        end=fallback_seed,
                        space_min=space_min,
                        space_max=space_max,
                    )
                except Exception:
                    pass

            if telemetry_enabled():
                try:
                    mode, default_range_id = _telemetry_context()
                    _emit_seed_event(
                        initial_seed_int,
                        mode=mode,
                        range_id=range_id_override or default_range_id,
                        used=True,
                        match_found=False,
                        range_observation=range_observation,
                    )
                except Exception:
                    pass

            log_with_context(
                logger,
                "INFO",
                f"📄 File complete: {lines} lines → {current_output_path}",
                **_keygen_log_context(
                    batch_id=batch_id,
                    index_within_batch=index_within_batch,
                    gpu_ids=selected_gpu_ids or None,
                ),
            )
            try:
                set_metric("last_rotation", time.time())
            except Exception:
                pass
        except Exception as e:
            rotation_grace = 5
            rotation_recent = False
            if rotation_info and rotation_info.get("timestamp"):
                rotation_recent = (time.time() - rotation_info["timestamp"]) <= rotation_grace

            if rotation_recent:
                log_with_context(
                    logger,
                    "INFO",
                    "ℹ️ Failed to parse %s during rotation (file inactive): %s",
                    current_output_path,
                    e,
                    **_keygen_log_context(
                        batch_id=batch_id,
                        index_within_batch=index_within_batch,
                        gpu_ids=selected_gpu_ids or None,
                    ),
                )
                return True
            log_with_context(
                logger,
                "WARNING",
                f"⚠️ Failed to parse {current_output_path}: {e}",
                **_keygen_log_context(
                    batch_id=batch_id,
                    index_within_batch=index_within_batch,
                    gpu_ids=selected_gpu_ids or None,
                ),
            )
            return False

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
    - Each VanitySearch batch is one process invocation and one output file
    - FILES_PER_BATCH defines the max pages (files) per macro-batch cycle
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
        log_with_context(
            logger,
            "INFO",
            "✅ Checkpoint loaded successfully",
            **_keygen_log_context(batch_id=KEYGEN_STATE["batch_id"]),
        )
    else:
        KEYGEN_STATE["batch_id"] = secrets.randbelow(1_000_000)
        KEYGEN_STATE["index_within_batch"] = secrets.randbelow(FILES_PER_BATCH)
        log_with_context(
            logger,
            "INFO",
            "🚀 No checkpoint found. Starting with randomized batch/index.",
            **_keygen_log_context(
                batch_id=KEYGEN_STATE["batch_id"],
                index_within_batch=KEYGEN_STATE["index_within_batch"],
            ),
        )

    # Initialize dashboard metrics so the GUI never shows N/A
    if get_metric("keys_generated_today", None) is None:
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

        last_addr_mode = None
        while True:
            if shutdown_evt and shutdown_evt.is_set():
                break

            try:
                addr_mode = get_metric("btc_address_mode", None)
                if addr_mode and addr_mode != last_addr_mode:
                    settings.ENABLE_P2PKH = addr_mode == "p2pkh"
                    settings.ENABLE_P2WPKH = addr_mode == "p2wpkh"
                    settings.ENABLE_TAPROOT = addr_mode == "taproot"
                    last_addr_mode = addr_mode
            except Exception:
                pass

            if pause_evt and pause_evt.is_set():
                # Emit a heartbeat log every 5s while paused so the user knows it's alive
                if (not pause_logged) or (time.time() - pause_log_ts > 5):
                    log_with_context(
                        logger,
                        "INFO",
                        "⏸️ Keygen paused. Waiting to resume...",
                        **_keygen_log_context(
                            batch_id=KEYGEN_STATE["batch_id"],
                            index_within_batch=KEYGEN_STATE["index_within_batch"],
                        ),
                    )
                    pause_logged = True
                    pause_log_ts = time.time()
                time.sleep(1)
                continue
            elif pause_logged:
                log_with_context(
                    logger,
                    "INFO",
                    "▶️ Keygen resumed.",
                    **_keygen_log_context(
                        batch_id=KEYGEN_STATE["batch_id"],
                        index_within_batch=KEYGEN_STATE["index_within_batch"],
                    ),
                )
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
                        mode, default_range_id = _telemetry_context()
                        pre_range_id = None
                        pre_range_observation = None
                        if mode == "btc_only":
                            space_min, space_max = _range_space()
                            pre_range_id = _format_range_id(seed, seed)
                            pre_range_observation = record_range_event(
                                mode=mode,
                                range_id=pre_range_id,
                                start=seed,
                                end=seed,
                                space_min=space_min,
                                space_max=space_max,
                            )
                        _emit_seed_event(
                            seed,
                            mode=mode,
                            range_id=pre_range_id or default_range_id,
                            used=False,
                            match_found=False,
                            range_observation=pre_range_observation,
                        )
                    except Exception:
                        pass

                success = run_vanitysearch_stream(
                    seed,
                    KEYGEN_STATE["batch_id"],
                    index,
                    pause_evt,
                    gpu_flag,
                    shutdown_evt,
                )
                if not success:
                    time.sleep(1)
                    continue

                # Save after each file so progress can resume mid-batch
                try:
                    log_with_context(
                        logger,
                        "INFO",
                        f"[Rotation] Completed part {index} of batch {KEYGEN_STATE['batch_id']}; starting next file",
                        **_keygen_log_context(
                            batch_id=KEYGEN_STATE["batch_id"],
                            index_within_batch=index,
                        ),
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
            log_with_context(
                logger,
                "INFO",
                f"Batch {KEYGEN_STATE['batch_id']} completed",
                **_keygen_log_context(batch_id=KEYGEN_STATE["batch_id"]),
            )

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
