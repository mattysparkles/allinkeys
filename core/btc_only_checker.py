# core/btc_only_checker.py

import os
import re
import bisect
import time
import json
from typing import Tuple, List, Optional

from config.settings import (
    VANITY_OUTPUT_DIR,
    ALL_BTC_ADDRESSES_DIR,
    ALL_BTC_RANGES_COUNT,
    BTC_RANGE_FILE_PATTERN,
    BTC_MIN_FILE_AGE_SEC,
)
from core.dashboard import set_metric, increment_metric
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
    if not os.path.exists(vanity_txt_path):
        return None
    if not safe_nonempty(vanity_txt_path, min_bytes=128):
        return None
    try:
        mtime = os.path.getmtime(vanity_txt_path)
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
        logger.warning(f"⚠️ On-demand sort failed for {os.path.basename(vanity_txt_path)}: {e}")
        return None


def prepare_btc_only_mode(use_all: bool, logger, skip_downloads: bool = False) -> None:
    """Prepare BTC-only checking mode."""
    global USE_ALL, FUNDed_SET, BOUNDARIES
    USE_ALL = use_all
    def _iter_daily():
        fp = find_latest_funded_file("btc")
        if not fp:
            return []
        with open(fp, "r", encoding="utf-8") as f:
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
            download_and_compare_address_lists()
            daily_iter = list(_iter_daily())
        else:
            daily_iter = list(_iter_daily())
        by_range = {i: [] for i in range(len(BOUNDARIES))}
        for addr in daily_iter:
            idx = route_address_to_range(addr, BOUNDARIES)
            by_range[idx].append(addr)
        for idx, addrs in by_range.items():
            if addrs:
                path = os.path.join(ALL_BTC_ADDRESSES_DIR, BTC_RANGE_FILE_PATTERN.format(idx))
                append_unique_sorted_to_range(path, addrs, logger)
        set_metric("btc_ranges_updated_today", True)
    else:
        if not skip_downloads:
            from core.downloader import download_and_compare_address_lists
            download_and_compare_address_lists()
        FUNDed_SET = set(_iter_daily())
        logger.info(f"Loaded {len(FUNDed_SET)} funded BTC addresses")


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
        logger.debug(f"Stability check failed for {os.path.basename(path)}: {e}")
        return False


def check_vanity_file_against_ranges(sorted_vanity_txt: str, all_btc_dir: str, logger) -> Tuple[int, int]:
    """
    Open an already-sorted vanity text file and check addresses against funded ranges/lists.
    Caller guarantees the file exists and is non-empty. This function should *not* do path existence checks,
    but it *should* fail softly if the file disappears between checks.
    """
    rows = 0
    matches = 0

    try:
        with open(sorted_vanity_txt, "r", encoding="utf-8") as f:
            for line in f:
                addr = line.strip()
                if not addr:
                    continue
                rows += 1
                matched = False
                if USE_ALL:
                    idx = route_address_to_range(addr, BOUNDARIES)
                    range_file = os.path.join(all_btc_dir, BTC_RANGE_FILE_PATTERN.format(idx))
                    matched = _binary_search_file(range_file, addr)
                else:
                    matched = addr in FUNDed_SET
                if matched:
                    matches += 1
                    try:
                        from core.alerts import alert_match
                        alert_match({"coin": "BTC", "address": addr, "csv_file": os.path.basename(sorted_vanity_txt)})
                    except Exception as e:
                        logger.warning(f"alert_match failed (non-fatal): {e}")
    except FileNotFoundError:
        # Another process could have rotated/deleted the file—just log and skip.
        logger.info(f"⏭️  sorted file vanished before reading: {os.path.basename(sorted_vanity_txt)}")
        return (0, 0)

    return (rows, matches)


def process_pending_vanity_outputs_once(logger):
    """
    Enumerate vanity_output/*.txt, and for each, obtain a .sorted file safely.
    Only call the range checker when a non-empty .sorted exists (or was created).
    Never crash if .sorted is missing; just skip and continue.
    """
    vanity_dir = VANITY_OUTPUT_DIR
    if not os.path.isdir(vanity_dir):
        logger.info(f"ℹ️ vanity_output directory not found: {vanity_dir}")
        return

    entries = sorted(
        [
            f
            for f in os.listdir(vanity_dir)
            if f.lower().endswith(".txt") and not f.lower().endswith(".part")
        ],
        key=lambda n: os.path.getmtime(os.path.join(vanity_dir, n)),
    )

    if not entries:
        logger.debug("🔍 No vanity .txt files to process this tick.")
        return

    for name in entries:
        txt_path = os.path.join(vanity_dir, name)
        if name in PROCESSED_VANITY:
            continue

        # Skip tiny or fresh files to avoid empty/not-ready churn
        if not safe_nonempty(txt_path, min_bytes=128):
            logger.info(f"⏭️  Skipping not-ready/empty file {name}")
            continue
        try:
            mtime = os.path.getmtime(txt_path)
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

        rows, matches = check_vanity_file_against_ranges(sorted_path, ALL_BTC_ADDRESSES_DIR, logger)
        logger.info(
            json.dumps(
                {
                    "event": "vanity_file_checked",
                    "file": os.path.basename(sorted_path),
                    "rows": rows,
                    "matches": matches,
                }
            )
        )
        increment_metric("btc_only_files_checked_today", 1)
        increment_metric("btc_only_matches_found_today", matches)
        increment_metric("addresses_checked_today.btc", rows)
        increment_metric("addresses_checked_lifetime.btc", rows)
        PROCESSED_VANITY.add(name)
        try:
            os.remove(sorted_path)
        except OSError:
            pass


def get_vanity_backlog_count() -> int:
    """Count pending VanitySearch output files awaiting check."""
    return len([
        f
        for f in os.listdir(VANITY_OUTPUT_DIR)
        if f.endswith(".txt")
        and not f.endswith(".part")
        and f not in PROCESSED_VANITY
    ])

