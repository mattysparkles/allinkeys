import os
import re
import subprocess
import time
import tempfile
from typing import Dict, List, Tuple, Optional

from config.settings import (
    GPU_BACKEND,
    FORCE_CPU_FALLBACK,
    VANITYSEARCH_BIN_CUDA,
    VANITYSEARCH_BIN_OPENCL,
    VANITYSEARCH_BIN_CPU,
    MIN_EXPECTED_GPU_MKEYS,
    MAX_OUTPUT_FILE_SIZE,
    ENABLE_P2PKH,
    ENABLE_P2WPKH,
    ENABLE_TAPROOT,
    DEFAULT_BTC_PATTERNS,
    DEFAULT_BTC_PATTERNS_BECH32,
    DEFAULT_BTC_PATTERNS_BECH32M,
    VANITY_TXT_DIR,
    VANITY_ROTATE_LINES,
    VANITY_MAX_BYTES,
    ENABLE_BC1_DEFAULT,
    VANITY_MODE,
    OCLVANITYGEN_PATH,
    find_vanitysearch_binary,
)
from core.logger import get_logger, log_message
from core.dashboard import set_metric, update_dashboard_stat
from core.utils.io_safety import atomic_open, atomic_commit
from core.vanity_io import RollingAtomicWriter, ensure_dir
from core.oclvanity_runner import run_oclvanitygen

logger = get_logger(__name__)
logger.info("Atomic writes enabled for vanity outputs (temp → rename). Empty outputs are skipped.")

# throttle warning frequency
_LAST_WARN: Dict[str, float] = {}


def _warn_once(name: str, msg: str, interval: float = 30.0) -> None:
    """Emit ``msg`` at most once per ``interval`` seconds for the given ``name``."""
    now = time.time()
    if now - _LAST_WARN.get(name, 0) >= interval:
        logger.warning(msg)
        _LAST_WARN[name] = now


def _run_binary(binary: str, args: List[str]) -> str:
    try:
        return subprocess.check_output([binary] + args, stderr=subprocess.STDOUT, text=True)
    except Exception as exc:
        logger.debug(f"Device probe failed for {binary}: {exc}")
        return ""


def list_devices() -> Dict[str, List[Tuple[int, str]]]:
    """Return available GPU devices for CUDA and OpenCL binaries."""
    devices: Dict[str, List[Tuple[int, str]]] = {}
    binaries = {
        "cuda": VANITYSEARCH_BIN_CUDA,
        "opencl": VANITYSEARCH_BIN_OPENCL,
    }
    for backend, bin_path in binaries.items():
        if not bin_path or not os.path.exists(bin_path):
            continue
        out = _run_binary(bin_path, ["-l"])
        entries: List[Tuple[int, str]] = []
        for line in out.splitlines():
            m = re.search(r"#(\d+)\s+(.+)$", line)
            if m:
                entries.append((int(m.group(1)), m.group(2).strip()))
        if entries:
            devices[backend] = entries
    return devices


_SELECTED_BACKEND: str = "cpu"
_SELECTED_DEVICE_ID: Optional[int] = None
_SELECTED_DEVICE_NAME: str = "CPU"
_SELECTED_BINARY: str = VANITYSEARCH_BIN_CPU or ""


def resolve_vanitysearch_binary(backend: str) -> str:
    """Return the VanitySearch binary path for ``backend``.

    Raises
    ------
    FileNotFoundError
        If no suitable binary is found for the requested ``backend``.
    """
    if backend == "cuda":
        path = VANITYSEARCH_BIN_CUDA or find_vanitysearch_binary()
    elif backend == "opencl":
        path = VANITYSEARCH_BIN_OPENCL
    elif backend == "oclvanitygen":
        path = OCLVANITYGEN_PATH
    else:
        path = VANITYSEARCH_BIN_CPU or find_vanitysearch_binary()
    if not path or not os.path.exists(path):
        raise FileNotFoundError("VanitySearch binary not found.")
    return path


def probe_device() -> Tuple[str, Optional[int], str, str]:
    """Select appropriate backend/device based on settings and availability."""
    global _SELECTED_BACKEND, _SELECTED_DEVICE_ID, _SELECTED_DEVICE_NAME, _SELECTED_BINARY

    backend = "cpu"
    device_id: Optional[int] = None
    device_name = "CPU"

    if GPU_BACKEND == "oclvanitygen":
        backend = "oclvanitygen"
        device_name = "OCLVanityGen"
    else:
        devices = list_devices()
        if not FORCE_CPU_FALLBACK:
            if GPU_BACKEND in ("cuda", "opencl") and devices.get(GPU_BACKEND):
                backend = GPU_BACKEND
                device_id, device_name = devices[GPU_BACKEND][0]
            elif GPU_BACKEND == "auto":
                for cand in ("cuda", "opencl"):
                    if devices.get(cand):
                        backend = cand
                        device_id, device_name = devices[cand][0]
                        break

    try:
        binary = resolve_vanitysearch_binary(backend)
    except FileNotFoundError:
        binary = None
    if backend in ("cuda", "opencl", "oclvanitygen") and (
        not binary or not os.path.exists(binary)
    ):
        _warn_once("binary_missing", f"GPU backend {backend} selected but binary missing. Falling back to CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        try:
            binary = resolve_vanitysearch_binary("cpu")
        except FileNotFoundError as e:
            logger.error(str(e))
            raise

    if backend != "cpu" and FORCE_CPU_FALLBACK:
        _warn_once("cpu_forced", "GPU available but FORCE_CPU_FALLBACK=True; using CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        try:
            binary = resolve_vanitysearch_binary("cpu")
        except FileNotFoundError as e:
            logger.error(str(e))
            raise

    if backend == "cpu" and GPU_BACKEND != "cpu":
        _warn_once("cpu_fallback", "GPU backend requested but CPU binary selected")

    if not binary or not os.path.exists(binary):
        raise FileNotFoundError("VanitySearch binary not found.")

    _SELECTED_BACKEND = backend
    _SELECTED_DEVICE_ID = device_id
    _SELECTED_DEVICE_NAME = device_name
    _SELECTED_BINARY = binary

    update_dashboard_stat({
        "vanitysearch_backend": backend,
        "vanitysearch_device_name": device_name,
    })
    logger.info(
        f"VanitySearch device: {device_name} | backend: {backend} | binary: {binary} | FORCE_CPU_FALLBACK={FORCE_CPU_FALLBACK}"
    )
    return backend, device_id, device_name, binary


def build_vanitysearch_args(hex_seed: str) -> List[Tuple[List[str], str]]:
    """Return a list of argument lists for each enabled address type."""
    jobs: List[Tuple[List[str], str]] = []

    backend = get_selected_backend()

    def _job(pattern: str) -> List[str]:
        if backend == "oclvanitygen":
            return [pattern]
        args = ["-s", hex_seed]
        _apply_mode_flags(args)
        args.append(pattern)
        return args

    if ENABLE_P2PKH:
        jobs.append((_job(DEFAULT_BTC_PATTERNS[0]), "p2pkh"))
    if ENABLE_P2WPKH:
        jobs.append((_job(DEFAULT_BTC_PATTERNS_BECH32[0]), "p2wpkh"))
    if ENABLE_TAPROOT:
        jobs.append((_job(DEFAULT_BTC_PATTERNS_BECH32M[0]), "taproot"))
    return jobs


def run_vanitysearch(
    seed_args: List[str],
    output_path: str,
    device_id: Optional[int],
    backend: str,
    timeout: int = 60,
    pause_event=None,
    addr_mode: str = "p2pkh",
) -> bool:
    """Execute VanitySearch with live speed parsing and atomic output handling."""
    if pause_event and pause_event.is_set():
        logger.info("Keygen paused; skipping VanitySearch job")
        return False

    if backend == "oclvanitygen":
        pattern = seed_args[0] if seed_args else DEFAULT_BTC_PATTERNS[0]
        return run_oclvanitygen(pattern, output_path, timeout=timeout, pause_event=pause_event, addr_mode=addr_mode)

    binary = resolve_vanitysearch_binary(backend)
    cmd = [binary] + seed_args
    if backend in ("cuda", "opencl") and device_id is not None:
        cmd += ["-gpu", str(device_id)]
    update_dashboard_stat("vanitysearch_addr_mode", addr_mode)
    logger.info(f"Executing: {' '.join(cmd)}")

    tmp_path, tmp_handle = atomic_open(output_path)
    buffer: List[str] = []
    valid_lines = 0
    addr_re = re.compile(
        r"^(?:PubAddr|PubAddress)\s*:\s*(\S+)|"  # legacy marker
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
            m = re.search(r"([0-9.]+)\s*([MK])?Key/s", line, re.IGNORECASE)
            if m:
                speed = float(m.group(1))
                if m.group(2) and m.group(2).upper() == "K":
                    speed /= 1000.0
                update_dashboard_stat("vanitysearch_current_mkeys", round(speed, 2))
                if backend != "cpu" and speed < MIN_EXPECTED_GPU_MKEYS:
                    _warn_once("low_speed", "Speed suggests CPU; check GPU selection")

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
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) >= MAX_OUTPUT_FILE_SIZE:
                proc.terminate()
        proc.wait()
    except Exception:
        logger.exception("Failed to execute VanitySearch")
        tmp_handle.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False

    tmp_handle.close()
    if valid_lines == 0:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        logger.info(f"No address lines emitted by VanitySearch for {addr_mode}")
        return False

    atomic_commit(tmp_path, output_path)
    return True


# Expose selected device info for callers
get_selected_backend = lambda: _SELECTED_BACKEND
get_selected_device_id = lambda: _SELECTED_DEVICE_ID
get_selected_device_name = lambda: _SELECTED_DEVICE_NAME


# --- New unified generator --------------------------------------------------


def _resolve_exe() -> Optional[str]:
    return find_vanitysearch_binary()


def _normalize_seed(seed_val: int) -> str:
    # VanitySearch allows decimal; keep it simple/stable.
    try:
        return str(int(seed_val))
    except Exception:
        return "0"


def _filter_patterns(pats: List[str]) -> List[str]:
    out = []
    for p in (pats or []):
        if not p or not isinstance(p, str):
            continue
        if p.lower().startswith("bc1") and not ENABLE_BC1_DEFAULT:
            continue
        out.append(p.strip())
    return out


def _apply_mode_flags(args: List[str]) -> None:
    mode = (VANITY_MODE or "both").lower()
    if mode == "both":
        args.append("-b")            # both compressed & uncompressed
    elif mode == "uncompressed":
        args.append("-u")            # uncompressed only
    else:
        pass                         # compressed-only (no flag)


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
    """
    Runs VanitySearch with correct CLI layout:
      - -b/-u are flags (no value)
      - Single prefix -> final positional arg
      - Multiple prefixes -> write to temp file and pass -i <file>
    Streams stdout into RollingAtomicWriter with atomic rotation.
    """
    out_dir = ensure_dir(VANITY_TXT_DIR)
    exe = _resolve_exe()
    if not exe:
        log_message("❌ VanitySearch binary not found.", "ERROR")
        return 0

    seed = _normalize_seed(seed_start)
    pats = _filter_patterns(patterns)
    if not pats:
        pats = ["1**"]  # safe default

    base = [exe, "-s", seed, "-q"]
    _apply_mode_flags(base)

    modes = [
        ("GPU", ["-gpu"]),
        ("OPENCL", ["-opencl"]),
        ("CPU", ["-cpu"]),
    ]

    # Build single- or multi-pattern invocation:
    tmpfile = None
    try:
        if len(pats) == 1:
            single_suffix = [pats[0]]
            multi_suffix: List[str] = []
        else:
            fd, tmpfile = tempfile.mkstemp(prefix="vanity_prefixes_", suffix=".txt", dir=out_dir)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(pats) + "\n")
            single_suffix = []
            multi_suffix = ["-i", tmpfile]

        addr_re = re.compile(
            r"^(?:PubAddr(?:ess)?\s*:\s*)?"  # optional "PubAddress:" prefix
            r"(1[1-9A-HJ-NP-Za-km-z]{25,34}|"  # legacy Base58 addresses
            r"3[1-9A-HJ-NP-Za-km-z]{25,34}|"  # P2SH addresses
            r"bc1[0-9ac-hj-np-z]{11,71})",    # Bech32 addresses
            re.IGNORECASE,
        )

        for mode_name, mode_flag in modes:
            args = base + mode_flag + multi_suffix + single_suffix
            writer = RollingAtomicWriter(
                out_dir, rotate_lines=VANITY_ROTATE_LINES, max_bytes=VANITY_MAX_BYTES, prefix="vanity"
            )
            total_lines = 0
            try:
                log_message(f"🧪 VanitySearch ({mode_name}): {' '.join(args)}", "INFO")
                proc = _popen_stream(args)
                last_line_ts = time.time()

                while True:
                    if stop_event and stop_event.is_set():
                        proc.terminate()
                        break

                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        if time.time() - last_line_ts > 5:
                            log_message("⏳ VanitySearch running (no output yet)...", "DEBUG")
                            last_line_ts = time.time()
                        time.sleep(0.05)
                        continue

                    last_line_ts = time.time()
                    striped = line.rstrip("\n")
                    if striped:
                        writer.write_line(striped)
                        if addr_re.match(striped):
                            total_lines += 1

                rc = proc.wait(timeout=10)
                if total_lines > 0:
                    log_message(
                        f"✅ VanitySearch finished ({mode_name}) with {total_lines} matches.",
                        "SUCCESS",
                    )
                    writer.close()
                    return total_lines
                else:
                    log_message(
                        f"⚠️ VanitySearch exited rc={rc}, matches={total_lines}. Trying next mode...",
                        "WARNING",
                    )
                    writer.abort()
            except Exception as e:
                log_message(f"⚠️ VanitySearch {mode_name} failed: {e}", "WARNING")
                writer.abort()
                continue

        log_message("❌ VanitySearch produced no output in any mode.", "ERROR")
        return 0

    finally:
        if tmpfile:
            try:
                os.remove(tmpfile)
            except Exception:
                pass
