# core/btc_ranges.py

import os
import gzip
import requests
from typing import Iterable, List, Tuple

from config.settings import (
    ALL_BTC_ADDRESSES_URL,
    ALL_BTC_ADDRESSES_DIR,
    ALL_BTC_RANGES_COUNT,
    ALL_BTC_GZ_LOCAL,
    BTC_RANGE_FILE_PATTERN,
)
from core.dashboard import set_metric


def download_with_progress(url: str, dest_path: str, logger) -> None:
    """Stream HTTP download with progress metrics."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    total = int(r.headers.get("Content-Length", 0))
    if total:
        set_metric("btc_ranges_download_size_bytes", total)
    downloaded = 0
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                set_metric("btc_ranges_download_progress_bytes", downloaded)
                if total:
                    pct = downloaded * 100 / total
                    logger.info(f"Downloading BTC addresses {downloaded}/{total} bytes ({pct:.2f}%)")
                else:
                    logger.info(f"Downloading BTC addresses {downloaded} bytes")
    logger.info("BTC address download complete")


def build_lexicographic_ranges_from_gz(
    gz_path: str, ranges_dir: str, ranges_count: int, logger
) -> None:
    """Split the sorted gz file into ``ranges_count`` range files."""
    os.makedirs(ranges_dir, exist_ok=True)
    # First pass to count lines
    total_lines = 0
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="ignore") as f:
        for _ in f:
            total_lines += 1
    lines_per = total_lines // ranges_count
    remainder = total_lines % ranges_count
    logger.info(f"Total BTC addresses: {total_lines}")
    # Second pass to write ranges
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="ignore") as f:
        idx = 0
        current_count = 0
        target = lines_per + (1 if idx < remainder else 0)
        outfile = None
        for line in f:
            if outfile is None:
                path = os.path.join(ranges_dir, BTC_RANGE_FILE_PATTERN.format(idx))
                outfile = open(path, "w", encoding="utf-8")
            outfile.write(line)
            current_count += 1
            if current_count >= target and idx < ranges_count - 1:
                outfile.close()
                idx += 1
                current_count = 0
                target = lines_per + (1 if idx < remainder else 0)
                outfile = None
        if outfile:
            outfile.close()
    set_metric("btc_ranges_files_ready", True)
    logger.info("BTC range files built")


def ensure_all_btc_ranges_ready(logger) -> None:
    """Ensure range files exist, downloading and building if needed."""
    os.makedirs(ALL_BTC_ADDRESSES_DIR, exist_ok=True)
    needed = [
        os.path.join(ALL_BTC_ADDRESSES_DIR, BTC_RANGE_FILE_PATTERN.format(i))
        for i in range(ALL_BTC_RANGES_COUNT)
    ]
    if all(os.path.exists(p) for p in needed):
        set_metric("btc_ranges_files_ready", True)
        return
    download_with_progress(ALL_BTC_ADDRESSES_URL, ALL_BTC_GZ_LOCAL, logger)
    build_lexicographic_ranges_from_gz(
        ALL_BTC_GZ_LOCAL, ALL_BTC_ADDRESSES_DIR, ALL_BTC_RANGES_COUNT, logger
    )


def get_range_boundaries(ranges_dir: str, ranges_count: int) -> List[Tuple[str, str]]:
    """Return (start, end) boundaries for each range file."""
    boundaries = []
    for i in range(ranges_count):
        path = os.path.join(ranges_dir, BTC_RANGE_FILE_PATTERN.format(i))
        start = end = ""
        if not os.path.exists(path):
            boundaries.append((start, end))
            continue
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            last = first
            for line in f:
                last = line.strip()
        boundaries.append((first, last))
    return boundaries


def append_unique_sorted_to_range(range_file: str, new_addresses_iter: Iterable[str], logger) -> None:
    """Merge new addresses into a sorted range file without duplicates."""
    new_sorted = sorted(set(a.strip() for a in new_addresses_iter if a.strip()))
    if not new_sorted:
        return
    temp_path = range_file + ".tmp"
    with open(range_file, "r", encoding="utf-8") as existing, open(
        temp_path, "w", encoding="utf-8"
    ) as out:
        existing_line = existing.readline().rstrip("\n")
        idx = 0
        while existing_line or idx < len(new_sorted):
            if existing_line and (idx >= len(new_sorted) or existing_line <= new_sorted[idx]):
                if idx < len(new_sorted) and existing_line == new_sorted[idx]:
                    idx += 1
                out.write(existing_line + "\n")
                existing_line = existing.readline().rstrip("\n")
            else:
                out.write(new_sorted[idx] + "\n")
                idx += 1
    os.replace(temp_path, range_file)
    logger.info(f"Updated range file {os.path.basename(range_file)} with {len(new_sorted)} addresses")


def route_address_to_range(addr: str, boundaries: List[Tuple[str, str]]) -> int:
    """Return index of range file that should contain ``addr``."""
    lo, hi = 0, len(boundaries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start, end = boundaries[mid]
        if start and addr < start:
            hi = mid - 1
        elif end and addr > end:
            lo = mid + 1
        else:
            return mid
    return max(min(lo, len(boundaries) - 1), 0)

