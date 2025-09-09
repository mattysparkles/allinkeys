import os
import re
import subprocess
import time
from pathlib import Path

from config.settings import (
    OCLVANITYGEN_PATH,
    MAX_OUTPUT_FILE_SIZE,
)
from core.logger import get_logger
from core.dashboard import update_dashboard_stat
from core.utils.io_safety import atomic_open, atomic_commit

logger = get_logger(__name__)


def run_oclvanitygen(pattern: str, output_path: str, timeout: int = 60, pause_event=None, addr_mode: str = "p2pkh") -> bool:
    """Execute oclvanitygen with basic output parsing and atomic writes."""
    if pause_event and pause_event.is_set():
        logger.info("Keygen paused; skipping OCLVanityGen job")
        return False

    if not OCLVANITYGEN_PATH or not Path(OCLVANITYGEN_PATH).exists():
        raise FileNotFoundError("OCLVanityGen binary not found.")

    cmd = [OCLVANITYGEN_PATH, pattern]
    update_dashboard_stat("vanitysearch_addr_mode", addr_mode)
    logger.info(f"Executing: {' '.join(cmd)}")

    tmp_path, tmp_handle = atomic_open(output_path)
    buffer = []
    valid_lines = 0
    addr_re = re.compile(
        r"^(?:PubAddr|PubAddress)\s*:\s*(\S+)|"
        r"^(1[1-9A-HJ-NP-Za-km-z]{25,34}|3[1-9A-HJ-NP-Za-km-z]{25,34}|bc1[0-9ac-hj-np-z]{11,71})$",
        re.IGNORECASE,
    )

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        start = time.time()
        for line in proc.stdout:
            buffer.append(line)
            if addr_re.match(line.strip()):
                valid_lines += 1
                if valid_lines == 1:
                    tmp_handle.writelines(buffer)
                else:
                    tmp_handle.write(line)
            elif valid_lines > 0:
                tmp_handle.write(line)

            if pause_event and pause_event.is_set():
                proc.terminate()
            if timeout and time.time() - start > timeout:
                proc.terminate()
            if Path(tmp_path).exists() and Path(tmp_path).stat().st_size >= MAX_OUTPUT_FILE_SIZE:
                proc.terminate()
        proc.wait()
    except Exception:
        logger.exception("Failed to execute OCLVanityGen")
        tmp_handle.close()
        p = Path(tmp_path)
        if p.exists():
            p.unlink(missing_ok=True)
        return False

    tmp_handle.close()
    if valid_lines == 0:
        p = Path(tmp_path)
        if p.exists():
            p.unlink(missing_ok=True)
        logger.info(f"No address lines emitted by OCLVanityGen for {addr_mode}")
        return False

    atomic_commit(tmp_path, output_path)
    return True
