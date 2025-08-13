# core/btc_only_checker.py

import os
import re
import bisect
import time
from typing import Tuple, List

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

logger = get_logger(__name__)
logger.info("Extractor auto-detect: PubAddr or raw-address mode.")

# Runtime globals
USE_ALL = False
FUNDed_SET = set()
BOUNDARIES = []


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


def check_vanity_file_against_ranges(sorted_vanity_txt: str, ranges_dir: str, logger) -> Tuple[int, int]:
    """Check addresses against BTC ranges or funded set."""
    original_path = sorted_vanity_txt[: -len(".sorted")]
    triples, lines = _extract_pubaddr_blocks(original_path, logger)
    addr_list = [addr for addr, _, _ in triples]
    rows = matches = 0
    with open(sorted_vanity_txt, "r", encoding="utf-8") as f:
        for line in f:
            addr = line.strip()
            if not addr:
                continue
            rows += 1
            matched = False
            if USE_ALL:
                idx = route_address_to_range(addr, BOUNDARIES)
                range_file = os.path.join(ranges_dir, BTC_RANGE_FILE_PATTERN.format(idx))
                matched = _binary_search_file(range_file, addr)
            else:
                matched = addr in FUNDed_SET
            if matched:
                matches += 1
                i = bisect.bisect_left(addr_list, addr)
                if i < len(triples) and triples[i][0] == addr:
                    start, end = triples[i][1], triples[i][2]
                    block_lines = lines[start : end + 1]
                    matches_file = original_path + ".matches.txt"
                    with open(matches_file, "a", encoding="utf-8") as mf:
                        mf.writelines(block_lines)
                        mf.write("---\n")
                    logger.info(
                        f"🎯 BTC match in {os.path.basename(original_path)} → wrote block to {os.path.basename(matches_file)}"
                    )
                try:
                    from core.alerts import alert_match
                    alert_match(
                        {
                            "coin": "BTC",
                            "address": addr,
                            "csv_file": os.path.basename(original_path),
                        }
                    )
                except Exception as e:
                    logger.warning(f"alert_match failed (non-fatal): {e}")
    return rows, matches


def process_pending_vanity_outputs_once(logger) -> int:
    """Process pending VanitySearch output files once."""
    processed = 0
    files = [f for f in os.listdir(VANITY_OUTPUT_DIR) if f.endswith(".txt")]
    for fname in files:
        path = os.path.join(VANITY_OUTPUT_DIR, fname)
        marker = path + ".btcchk"
        if os.path.exists(marker):
            continue
        # Skip if not stable yet
        if not _is_file_stable(path, logger):
            increment_metric("vanity_unstable_skips", 1)
            continue
        sorted_path = path + ".sorted"
        sort_addresses_in_file(path, sorted_path, logger)
        rows, matches = check_vanity_file_against_ranges(sorted_path, ALL_BTC_ADDRESSES_DIR, logger)
        increment_metric("btc_only_files_checked_today", 1)
        increment_metric("btc_only_matches_found_today", matches)
        increment_metric("addresses_checked_today.btc", rows)
        increment_metric("addresses_checked_lifetime.btc", rows)
        with open(marker, "w", encoding="utf-8") as m:
            m.write(f"checked={rows} matches={matches}\n")
        os.remove(sorted_path)
        processed += 1
    return processed


def get_vanity_backlog_count() -> int:
    """Count pending VanitySearch output files awaiting check."""
    return len([
        f
        for f in os.listdir(VANITY_OUTPUT_DIR)
        if f.endswith(".txt") and not os.path.exists(os.path.join(VANITY_OUTPUT_DIR, f + ".btcchk"))
    ])

