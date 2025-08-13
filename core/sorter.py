import os
from typing import Optional
from core.utils.io_safety import safe_nonempty

PUBADDR_MARK = "PubAddr"

def sort_if_ready(input_path: str, logger, min_bytes: int = 128) -> Optional[str]:
    """
    If input_path exists, is non-empty, and contains parsable addresses,
    write a `.sorted` sibling with sorted/unique addresses.
    Returns the sorted path on success, None on no-op / no addresses.
    Never creates an empty .sorted.
    """
    if not safe_nonempty(input_path, min_bytes=min_bytes):
        logger.info(f"Skipping extractor for empty/not-ready file {os.path.basename(input_path)}")
        return None

    sorted_path = input_path + ".sorted"
    addrs = set()

    try:
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if PUBADDR_MARK in s:
                    # Legacy block format: extract final address token after marker
                    # (Adjust this slice logic to your actual PubAddr line structure)
                    parts = s.split()
                    last = parts[-1]
                    addrs.add(last.lower() if last.lower().startswith("bc1") else last)
                else:
                    # Raw-address-per-line mode
                    if s[0] in ("1", "3") or s.lower().startswith("bc1"):
                        addrs.add(s.lower() if s.lower().startswith("bc1") else s)
    except FileNotFoundError:
        return None

    if not addrs:
        # No addresses parsed → do not create a sidecar
        return None

    # Write deterministically sorted output
    try:
        with open(sorted_path, "w", encoding="utf-8") as out:
            for a in sorted(addrs):
                out.write(a + "\n")
    except OSError as e:
        logger.warning(f"⚠️ Failed writing sidecar for {os.path.basename(input_path)}: {e}")
        return None

    return sorted_path
