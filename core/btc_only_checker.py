# core/btc_only_checker.py

import os
import re
import bisect
import time
import json
from typing import Tuple, List, Optional
from pathlib import Path

from config.settings import (
    ALL_BTC_RANGES_COUNT,
    BTC_RANGE_FILE_PATTERN,
    BTC_MIN_FILE_AGE_SEC,
)
from config.directories import VANITY_OUTPUT_DIR, ALL_BTC_ADDRESSES_DIR
from core.dashboard import set_metric, increment_metric, update_dashboard_stat, get_metric
from utils.file_utils import find_latest_funded_file
from core.btc_ranges import (
    ensure_all_btc_ranges_ready,
    get_range_boundaries,
    route_address_to_range,
    append_unique_sorted_to_range,
)
from core.logger import get_logger
from core.utils.io_safety import safe_nonempty
from core.sorter import sort_if_ready

logger = get_logger(__name__)
logger.info("Extractor auto-detect: PubAddr or raw-address mode.")

# Runtime globals
USE_ALL = False
FUNDed_SET = set()
BOUNDARIES = []


DEBOUNCE_SECONDS = 2  # avoid racing files that are still being written
PROCESSED_VANITY = set()  # track processed vanity outputs to avoid rechecks


def ensure_sorted_or_skip(vanity_txt_path: str, logger) -> Optional[str]:
    """
    Return path to .sorted if it exists and is non-empty.
    If absent, attempt to create it via sort_if_ready() when the source is ready.
    Returns None if not available/ready; caller should skip without error.
    """
    sorted_path = vanity_txt_path + ".sorted"

    # If .sorted is already present & non-empty, use it
    if safe_nonempty(sorted_path, min_bytes=128):
        return sorted_path

    # Source must exist, be non-empty, and not too "fresh"
    if not Path(vanity_txt_path).exists():
        return None
    if not safe_nonempty(vanity_txt_path, min_bytes=128):
        return None
    try:
        mtime = Path(vanity_txt_path).stat().st_mtime
        if (time.time() - mtime) < DEBOUNCE_SECONDS:
            # Too fresh; let writer/extractor finish
            return None
    except OSError:
        return None

    # Try to make .sorted on-demand (auto-detects PubAddr vs raw-address)
    try:
        created = sort_if_ready(vanity_txt_path, logger)
        if created and safe_nonempty(created, min_bytes=128):
            return created
        return None
    except Exception as e:
        logger.warning(f"⚠️ On-demand sort failed for {Path(vanity_txt_path).name}: {e}")
        return None


def prepare_btc_only_mode(use_all: bool, logger, skip_downloads: bool = False) -> None:
    """Prepare BTC-only checking mode."""
    global USE_ALL, FUNDed_SET, BOUNDARIES
    USE_ALL = use_all

    funded_fp: Optional[str] = None

    def _iter_daily():
        nonlocal funded_fp
        funded_fp = find_latest_funded_file("btc")
        if not funded_fp:
            return []
        with open(funded_fp, "r", encoding="utf-8") as f:
            for line in f:
                addr = line.strip()
                if addr:
                    yield addr

    if use_all:
        ensure_all_btc_ranges_ready(logger)
        BOUNDARIES = get_range_boundaries(ALL_BTC_ADDRESSES_DIR, ALL_BTC_RANGES_COUNT)
        daily_iter = []
        if not skip_downloads:
            from core.downloader import download_and_compare_address_lists
            download_and_compare_address_lists(coins=["btc"])
            daily_iter = list(_iter_daily())
        else:
            daily_iter = list(_iter_daily())
        if funded_fp:
            logger.info(
                f"Using funded list {Path(funded_fp).name} with {len(daily_iter)} addresses"
            )
        else:
            logger.warning("No funded BTC address file found")
        by_range = {i: [] for i in range(len(BOUNDARIES))}
        for addr in daily_iter:
            idx = route_address_to_range(addr, BOUNDARIES)
            by_range[idx].append(addr)
        for idx, addrs in by_range.items():
            if addrs:
                path = str((Path(ALL_BTC_ADDRESSES_DIR) / BTC_RANGE_FILE_PATTERN.format(idx)).resolve())
                append_unique_sorted_to_range(path, addrs, logger)
        set_metric("btc_ranges_updated_today", True)
    else:
        if not skip_downloads:
            from core.downloader import download_and_compare_address_lists
            download_and_compare_address_lists(coins=["btc"])
        FUNDed_SET = set(_iter_daily())
        if funded_fp:
            logger.info(
                f"Using funded list {Path(funded_fp).name} with {len(FUNDed_SET)} addresses"
            )
        else:
            logger.warning("No funded BTC address file found")


def _extract_pubaddr_blocks(path: str, logger) -> Tuple[List[Tuple[str, int, int]], List[str]]:
    """Extract PubAddr blocks from a VanitySearch output file."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    pattern = re.compile(r"^\s*(pubaddr|pubaddress)\s*:\s*(\S+)", re.IGNORECASE)
    triples: List[Tuple[str, int, int]] = []
    for idx, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            addr = m.group(2)
            start = max(0, idx - 2)
            end = idx
            triples.append((addr, start, end))
    triples.sort(key=lambda t: t[0])
    return triples, lines


def sort_addresses_in_file(input_txt: str, output_txt: str, logger) -> None:
    """Extract BTC addresses from ``input_txt`` and write a sorted sidecar."""
    if not safe_nonempty(input_txt):
        logger.info(
            f"Skipping extractor for empty/not-ready file {os.path.basename(input_txt)}"
        )
        return

    with open(input_txt, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    marker_re = re.compile(r"^(?:PubAddr|PubAddress)\s*:\s*(\S+)", re.IGNORECASE)
    addresses: List[str] = []
    for ln in lines:
        m = marker_re.match(ln)
        if m:
            addresses.append(m.group(1))

    if not addresses:
        raw_re = re.compile(
            r"^(1[1-9A-HJ-NP-Za-km-z]{25,34}|3[1-9A-HJ-NP-Za-km-z]{25,34}|bc1[0-9ac-hj-np-z]{11,71})$"
        )
        for ln in lines:
            if raw_re.match(ln):
                addresses.append(ln)

    if not addresses:
        logger.info(
            f"No addresses detected; skipping extractor for {os.path.basename(input_txt)}"
        )
        return

    addresses.sort()
    with open(output_txt, "w", encoding="utf-8") as f:
        for addr in addresses:
            f.write(addr + "\n")
    logger.info(
        f"✅ Sorted {len(addresses)} BTC addresses to sidecar: {os.path.basename(output_txt)}"
    )
def _binary_search_file(file_path: str, target: str) -> bool:
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    i = bisect.bisect_left(lines, target + "\n")
    return i < len(lines) and lines[i].strip() == target


def _is_file_stable(path: str, logger) -> bool:
    """
    A file is considered 'stable' if:
      - Its mtime is older than BTC_MIN_FILE_AGE_SEC, AND
      - Its size does not change over BTC_FILE_STABILITY_POLLS spaced by
        BTC_FILE_STABILITY_INTERVAL_SEC.
    We never block long; worst case ~BTC_FILE_STABILITY_WINDOW_SEC.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return False

    age_ok = (time.time() - st.st_mtime) >= BTC_MIN_FILE_AGE_SEC
    if not age_ok:
        return False

    from config.settings import (
        BTC_FILE_STABILITY_POLLS, BTC_FILE_STABILITY_INTERVAL_SEC
    )
    try:
        last = os.path.getsize(path)
        for _ in range(BTC_FILE_STABILITY_POLLS):
            time.sleep(BTC_FILE_STABILITY_INTERVAL_SEC)
            cur = os.path.getsize(path)
            if cur != last:
                return False
            last = cur
        return True
    except Exception as e:
        logger.debug(f"Stability check failed for {Path(path).name}: {e}")
        return False


def check_vanity_file_against_ranges(
    sorted_vanity_txt: str, all_btc_dir: str, logger
) -> Tuple[int, int, List[Tuple[int, str]]]:
    """
    Open an already-sorted vanity text file and check addresses against funded ranges/lists.
    Caller guarantees the file exists and is non-empty. This function should *not* do path existence checks,
    but it *should* fail softly if the file disappears between checks.
    """
    rows = 0
    matches = 0
    matched_lines: List[Tuple[int, str]] = []

    try:
        with open(sorted_vanity_txt, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                addr = line.strip()
                if not addr:
                    continue
                rows += 1
                matched = False
                if USE_ALL:
                    idx = route_address_to_range(addr, BOUNDARIES)
                    range_file = str((Path(all_btc_dir) / BTC_RANGE_FILE_PATTERN.format(idx)).resolve())
                    matched = _binary_search_file(range_file, addr)
                else:
                    matched = addr in FUNDed_SET
                if matched:
                    matches += 1
                    matched_lines.append((line_num, addr))
                    try:
                        from core.alerts import alert_match

                        alert_match(
                            {
                                "coin": "BTC",
                                "address": addr,
                                "csv_file": Path(sorted_vanity_txt).name,
                            }
                        )
                    except Exception as e:
                        logger.warning(f"alert_match failed (non-fatal): {e}")
    except FileNotFoundError:
        # Another process could have rotated/deleted the file—just log and skip.
        logger.info(
            f"⏭️  sorted file vanished before reading: {Path(sorted_vanity_txt).name}"
        )
        return (0, 0, [])

    return (rows, matches, matched_lines)


def process_pending_vanity_outputs_once(logger) -> None:
    """Enumerate ``output/vanity_output`` (or configured ``VANITY_OUTPUT_DIR``).

    For each ``.txt`` file, obtain a ``.sorted`` file safely. Only call the range
    checker when a non-empty ``.sorted`` exists (or was created). Never crash if
    ``.sorted`` is missing; just skip and continue.
    """
    vanity_dir = Path(VANITY_OUTPUT_DIR)
    if not vanity_dir.is_dir():
        logger.info(f"ℹ️ vanity output directory not found: {vanity_dir}")
        return

    entries = sorted(
        [p.name for p in vanity_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt" and not p.name.lower().endswith(".part")],
        key=lambda n: (vanity_dir / n).stat().st_mtime,
    )

    if not entries:
        logger.debug("🔍 No vanity .txt files to process this tick.")
        return

    for name in entries:
        txt_path = str((vanity_dir / name).resolve())
        if name in PROCESSED_VANITY:
            continue

        # Skip tiny or fresh files to avoid empty/not-ready churn
        if not safe_nonempty(txt_path, min_bytes=128):
            logger.info(f"⏭️  Skipping not-ready/empty file {name}")
            continue
        try:
            mtime = Path(txt_path).stat().st_mtime
            if (time.time() - mtime) < DEBOUNCE_SECONDS:
                logger.debug(f"⏳ Deferring fresh file {name} (debounce {DEBOUNCE_SECONDS}s)")
                continue
        except OSError:
            continue

        sorted_path = ensure_sorted_or_skip(txt_path, logger)
        if not sorted_path:
            logger.debug(f"⏭️  .sorted not available yet for {name}; will retry later.")
            continue

        # Guard: .sorted must be present & non-empty
        if not safe_nonempty(sorted_path, min_bytes=128):
            logger.info(f"⏭️  Skipping empty .sorted for {name}")
            continue

        rows, matches, match_lines = check_vanity_file_against_ranges(
            sorted_path, ALL_BTC_ADDRESSES_DIR, logger
        )
        logger.info(
            f"{Path(sorted_path).name} was checked, ({rows}) addresses ({matches}) matches found"
        )
        for line_no, addr in match_lines:
            logger.info(f"Line {line_no}: {addr}")
        logger.info(
            json.dumps(
                {
                    "event": "vanity_file_checked",
                    "file": Path(sorted_path).name,
                    "rows": rows,
                    "matches": matches,
                }
            )
        )
        increment_metric("btc_only_files_checked_today", 1)
        increment_metric("btc_only_matches_found_today", matches)
        increment_metric("addresses_checked_today.btc", rows)
        increment_metric("addresses_checked_lifetime.btc", rows)
        try:
            update_dashboard_stat(
                "addresses_checked_today",
                get_metric("addresses_checked_today"),
            )
            update_dashboard_stat(
                "addresses_checked_lifetime",
                get_metric("addresses_checked_lifetime"),
            )
        except Exception:
            pass
        PROCESSED_VANITY.add(name)
        try:
            Path(sorted_path).unlink(missing_ok=True)
        except OSError:
            pass


def get_vanity_backlog_count() -> int:
    """Count pending VanitySearch output files awaiting check."""
    vdir = Path(VANITY_OUTPUT_DIR)
    return len([
        f.name
        for f in vdir.iterdir()
        if f.is_file() and f.name.endswith(".txt") and not f.name.endswith(".part") and f.name not in PROCESSED_VANITY
    ])


def btc_only_checker_loop(
    shared_metrics=None,
    shutdown_event=None,
    pause_event=None,
    log_q=None,
    use_all: bool = False,
    skip_downloads: bool = False,
) -> None:
    """Continuously process VanitySearch outputs in BTC-only mode.

    This wrapper initialises shared metrics and periodically invokes
    :func:`process_pending_vanity_outputs_once` so the GUI can display
    progress in the simplified ``--only btc`` flow.
    """

    from core.logger import initialize_logging
    from core.worker_bootstrap import ensure_metrics_ready, _safe_set_metric
    from core.dashboard import register_control_events, set_thread_health

    initialize_logging(log_q)

    try:
        ensure_metrics_ready(shared_metrics)
        register_control_events(shutdown_event, pause_event, module="btc_check")
        _safe_set_metric("status.btc_check", "Running")
        set_thread_health("btc_check", True)
    except Exception as e:
        logger.exception(f"btc_only_checker_loop init failed: {e}")

    prepare_btc_only_mode(use_all, logger, skip_downloads=skip_downloads)

    while not (shutdown_event and shutdown_event.is_set()):
        if pause_event and pause_event.is_set():
            time.sleep(1)
            continue
        try:
            process_pending_vanity_outputs_once(logger)
            set_metric("vanity_backlog_count", get_vanity_backlog_count())
        except Exception as e:
            logger.warning(f"btc_only_checker_loop tick failed: {e}")
        time.sleep(1)

    _safe_set_metric("status.btc_check", "Stopped")
    try:
        set_thread_health("btc_check", False)
    except Exception:
        logger.warning("Failed to update btc_check thread health", exc_info=True)
