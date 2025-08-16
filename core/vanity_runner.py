import os, shutil, subprocess, time
from typing import List, Optional

from config.settings import (
    VANITY_TXT_DIR, VANITY_ROTATE_LINES, VANITY_MAX_BYTES, ENABLE_BC1_DEFAULT
)
from core.vanity_io import RollingAtomicWriter, ensure_dir
from core.dashboard import log_message


def _bin_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin")


def _which(path: str) -> Optional[str]:
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    found = shutil.which(path)
    return found if found else (path if os.path.isfile(path) else None)


def _resolve_vanity_binaries() -> dict:
    bin_dir = _bin_dir()
    candidates = [
        os.path.join(bin_dir, "vanitysearch.exe"),
        os.path.join(bin_dir, "vanitysearch_cuda.exe"),
        "vanitysearch.exe",
        "vanitysearch_cuda.exe",
    ]
    exe = next((p for p in candidates if _which(p)), None)
    return {"GPU": exe, "OPENCL": exe, "CPU": exe} if exe else {"GPU": None, "OPENCL": None, "CPU": None}


def _build_patterns(patterns: Optional[List[str]]) -> List[str]:
    pat_args: List[str] = []
    if patterns:
        for p in patterns:
            if p and isinstance(p, str):
                if p.lower().startswith("bc1") and not ENABLE_BC1_DEFAULT:
                    continue
                pat_args.extend(["-b", p])
    return pat_args or ["-b", "1**"]


def _popen_stream(args: List[str]) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )


def run_vanity_generator(seed_start: int, patterns: List[str], stop_event=None) -> int:
    out_dir = ensure_dir(VANITY_TXT_DIR)
    bins = _resolve_vanity_binaries()
    if not any(bins.values()):
        log_message("❌ VanitySearch binary not found (expected vanitysearch.exe).", "ERROR")
        return 0
    pat_args = _build_patterns(patterns)
    base = ["-s", str(seed_start), "-q"] + pat_args
    modes = [("GPU", ["-gpu"]), ("OPENCL", ["-opencl"]), ("CPU", ["-cpu"])]
    writer = RollingAtomicWriter(out_dir, rotate_lines=VANITY_ROTATE_LINES,
                                 max_bytes=VANITY_MAX_BYTES, prefix="vanity")
    total_lines = 0
    for mode_name, mode_flag in modes:
        exe = bins.get(mode_name)
        if not exe:
            continue
        args = [exe] + base + mode_flag
        try:
            log_message(f"🧪 Trying VanitySearch ({mode_name}): {' '.join(args)}", "INFO")
            proc = _popen_stream(args)
            last_ts = time.time()
            while True:
                if stop_event and stop_event.is_set():
                    proc.terminate(); break
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    if time.time() - last_ts > 5:
                        log_message("⏳ VanitySearch running (no output yet)...", "DEBUG")
                        last_ts = time.time()
                    time.sleep(0.05)
                    continue
                last_ts = time.time()
                if ("1" in line) or ("3" in line) or ("bc1" in line):
                    writer.write_line(line.rstrip("\n"))
                    total_lines += 1
            rc = proc.wait(timeout=10)
            if rc == 0 and total_lines > 0:
                log_message(f"✅ VanitySearch finished ({mode_name}) with {total_lines} lines.", "SUCCESS")
                writer.close(); return total_lines
            log_message(f"⚠️ VanitySearch exited rc={rc}, lines={total_lines}. Trying next mode...", "WARNING")
        except Exception as e:
            log_message(f"⚠️ VanitySearch {mode_name} mode failed: {e}", "WARNING")
            continue
    if total_lines > 0:
        writer.close()
        log_message(f"✅ Finalized partial output with {total_lines} lines after fallbacks.", "INFO")
        return total_lines
    try:
        writer.close()
    except Exception:
        pass
    log_message("❌ VanitySearch produced no output in any mode.", "ERROR")
    return 0

