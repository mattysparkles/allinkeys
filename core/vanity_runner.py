import os
import re
import subprocess
import time
import json
import shutil
from typing import Dict, List, Tuple, Optional

from config.settings import (
    GPU_BACKEND,
    FORCE_CPU_FALLBACK,
    USE_CPU_FALLBACK,
    MIN_EXPECTED_GPU_MKEYS,
    ENABLE_P2PKH,
    ENABLE_P2WPKH,
    ENABLE_TAPROOT,
    DEFAULT_BTC_PATTERNS,
    DEFAULT_BTC_PATTERNS_BECH32,
    DEFAULT_BTC_PATTERNS_BECH32M,
    VANITY_ROTATE_LINES,
    VANITY_MAX_BYTES,
    BASE_DIR,
)
from core.logger import get_logger
from core.dashboard import update_dashboard_stat
from core.vanity_io import RollingAtomicWriter

logger = get_logger(__name__)
logger.info("Streaming vanity output via .part files → atomic rename enabled")

# Optional Windows file locking to keep .part files visible and protected
try:  # pragma: no cover - only effective on Windows
    import msvcrt

    def _lock_file(handle):
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle):
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
except Exception:  # pragma: no cover - platform without msvcrt
    def _lock_file(handle):
        return None

    def _unlock_file(handle):
        return None

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


def probe_vanity_capabilities(vanity_path: str) -> dict:
    """Run a quick capability check for VanitySearch to discover GPU usability.

    Returns a dictionary with keys:
        {
          "binary_found": bool,
          "gpu_supported": bool,
          "gpu_vendor": "nvidia" | "amd" | None,
          "version": str | None,
          "raw_banner": str,
        }
    Strategy: invoke the binary with ``--help`` and parse banner lines for
    CUDA/OpenCL hints.  Non-zero exit codes are treated as failure and the
    full output is logged for troubleshooting.
    """
    info = {
        "binary_found": False,
        "gpu_supported": False,
        "gpu_vendor": None,
        "version": None,
        "raw_banner": "",
    }
    if not vanity_path or not os.path.exists(vanity_path):
        return info
    info["binary_found"] = True
    try:
        proc = subprocess.run([vanity_path, "--help"], capture_output=True, text=True)
        info["raw_banner"] = (proc.stdout or "") + (proc.stderr or "")
        for line in info["raw_banner"].splitlines():
            m = re.search(r"VanitySearch\s*v?([0-9.]+)", line, re.I)
            if m:
                info["version"] = m.group(1)
            if "CUDA" in line.upper():
                info["gpu_supported"] = True
                info["gpu_vendor"] = "nvidia"
            if "OPENCL" in line.upper():
                info["gpu_supported"] = True
                if info["gpu_vendor"] is None:
                    info["gpu_vendor"] = "amd"
        if proc.returncode != 0:
            logger.error(
                f"Capability probe failed for {vanity_path} (exit {proc.returncode})\n{info['raw_banner']}"
            )
            info["gpu_supported"] = False
    except Exception as exc:  # pragma: no cover - unexpected failures
        logger.error(f"Capability probe failed for {vanity_path}: {exc}", exc_info=True)
    return info


def list_devices() -> Dict[str, List[Tuple[int, str]]]:
    """Return available GPU devices for CUDA and OpenCL binaries."""
    devices: Dict[str, List[Tuple[int, str]]] = {}
    binaries = {
        "cuda": VANITYSEARCH_BIN_CUDA,
        "opencl": VANITYSEARCH_BIN_OPENCL,
    }
    for backend, bin_path in binaries.items():
        if not os.path.exists(bin_path):
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
_SELECTED_BINARY: str = VANITYSEARCH_BIN_CPU


def resolve_vanitysearch_binary(backend: str) -> str:
    """Resolve VanitySearch executable path for the requested ``backend``."""
    bin_dir = os.path.join(BASE_DIR, "bin")
    names = []
    if backend == "cuda":
        names = ["vanitysearch_cuda.exe", "vanitysearch.exe"]
    elif backend == "opencl":
        names = ["vanitysearch_opencl.exe", "vanitysearch.exe"]
    else:
        names = ["vanitysearch.exe"]
    for n in names:
        candidate = os.path.join(bin_dir, n)
        if os.path.exists(candidate):
            return candidate
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    # Fallback to first candidate in bin_dir
    return os.path.join(bin_dir, names[0])


def probe_device() -> Tuple[str, Optional[int], str, str]:
    """Select appropriate backend/device based on settings and availability."""
    global _SELECTED_BACKEND, _SELECTED_DEVICE_ID, _SELECTED_DEVICE_NAME, _SELECTED_BINARY

    devices = list_devices()
    backend = "cpu"
    device_id: Optional[int] = None
    device_name = "CPU"

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

    binary = resolve_vanitysearch_binary(backend)
    if backend in ("cuda", "opencl") and not os.path.exists(binary):
        _warn_once("binary_missing", f"GPU backend {backend} selected but binary missing. Falling back to CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        binary = resolve_vanitysearch_binary("cpu")

    if backend != "cpu" and FORCE_CPU_FALLBACK:
        _warn_once("cpu_forced", "GPU available but FORCE_CPU_FALLBACK=True; using CPU")
        backend = "cpu"
        device_id = None
        device_name = "CPU"
        binary = resolve_vanitysearch_binary("cpu")

    capabilities = probe_vanity_capabilities(binary)
    if backend != "cpu" and not capabilities.get("gpu_supported"):
        msg = "GPU not available or unsupported by vanitysearch binary"
        if USE_CPU_FALLBACK:
            logger.error(f"{msg} → switched to CPU; set USE_CPU_FALLBACK=False to disallow")
            backend = "cpu"
            device_id = None
            device_name = "CPU"
            binary = resolve_vanitysearch_binary("cpu")
        else:
            logger.error(f"{msg}; aborting vanity worker")
            update_dashboard_stat("vanitysearch_backend", "unavailable")
            raise RuntimeError(msg)

    if backend == "cpu" and GPU_BACKEND != "cpu":
        _warn_once("cpu_fallback", "GPU backend requested but CPU binary selected")

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
    """Compose VanitySearch argument lists for each requested address family.

    The binary's ``--help`` output is parsed at runtime to determine the
    correct switches for legacy P2PKH, Bech32 (v0) and Bech32m (v1) so newer
    builds do not break when flags change.  This ensures ``bc1`` support does
    not disable classic ``1…`` generation.
    """

    binary = _SELECTED_BINARY or resolve_vanitysearch_binary(_SELECTED_BACKEND)
    help_text = _run_binary(binary, ["--help"])

    switches = {"p2pkh": "-1", "bech32": "-b", "bech32m": "-3"}
    for line in help_text.splitlines():
        line_l = line.lower()
        m = re.search(r"-(\S+)", line)
        flag = f"-{m.group(1)}" if m else None
        if "p2pkh" in line_l or "legacy" in line_l:
            switches["p2pkh"] = flag or switches["p2pkh"]
        if "bech32m" in line_l or "taproot" in line_l:
            switches["bech32m"] = flag or switches["bech32m"]
        elif "bech32" in line_l:
            switches["bech32"] = flag or switches["bech32"]

    jobs: List[Tuple[List[str], str]] = []
    base = ["-s", hex_seed]

    # Legacy P2PKH addresses are included unless explicitly disabled via
    # ``ENABLE_P2PKH``.  This prevents accidental suppression when bc1 modes
    # are enabled.
    if ENABLE_P2PKH:
        jobs.append((base + [switches["p2pkh"], DEFAULT_BTC_PATTERNS[0]], "p2pkh"))
    if ENABLE_P2WPKH:
        jobs.append((base + [switches["bech32"], DEFAULT_BTC_PATTERNS_BECH32[0]], "p2wpkh"))
    if ENABLE_TAPROOT:
        jobs.append((base + [switches["bech32m"], DEFAULT_BTC_PATTERNS_BECH32M[0]], "taproot"))
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
    """Execute VanitySearch streaming stdout to ``output_path`` using a safe writer."""
    if pause_event and pause_event.is_set():
        logger.info("Keygen paused; skipping VanitySearch job")
        return False

    enable_bc1 = ENABLE_P2WPKH or ENABLE_TAPROOT

    def _run_once(b: str) -> bool:
        binary = resolve_vanitysearch_binary(b)
        cmd = [binary] + seed_args
        if b in ("cuda", "opencl") and device_id is not None:
            cmd += ["-gpu", str(device_id)]
        mode_name = {"cuda": "GPU-CUDA", "opencl": "GPU-OpenCL/AMD", "cpu": "CPU"}.get(b, b)

        sanitized: List[str] = []
        skip = False
        for c in cmd:
            if skip:
                skip = False
                continue
            if c == "-s":
                sanitized.extend(["-s", "<seed>"])
                skip = True
            else:
                sanitized.append(c)
        logger.info(f"Executing VanitySearch ({mode_name}): {' '.join(sanitized)}")
        update_dashboard_stat("vanitysearch_addr_mode", addr_mode)

        writer = RollingAtomicWriter(output_path, VANITY_ROTATE_LINES, VANITY_MAX_BYTES)
        addr_re = re.compile(
            r"^(?:PubAddr|PubAddress)\s*:\s*(\S+)|"
            r"^(1[1-9A-HJ-NP-Za-km-z]{25,34}|3[1-9A-HJ-NP-Za-km-z]{25,34}|bc1[0-9ac-hj-np-z]{11,71})$",
            re.IGNORECASE,
        )
        valid_lines = 0
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            start = time.time()
            for line in proc.stdout:
                m = re.search(r"([0-9.]+)\s*([MK])?Key/s", line, re.IGNORECASE)
                if m:
                    speed = float(m.group(1))
                    if m.group(2) and m.group(2).upper() == "K":
                        speed /= 1000.0
                    update_dashboard_stat("vanitysearch_current_mkeys", round(speed, 2))
                    if b != "cpu" and speed < MIN_EXPECTED_GPU_MKEYS:
                        _warn_once("low_speed", "Speed suggests CPU; check GPU selection")

                stripped = line.strip()
                if addr_re.match(stripped):
                    addr = stripped.split()[0]
                    if addr.lower().startswith("bc1") and not enable_bc1:
                        continue
                    valid_lines += 1
                    if writer.write(line):
                        proc.terminate()
                elif valid_lines > 0:
                    if writer.write(line):
                        proc.terminate()

                if pause_event and pause_event.is_set():
                    proc.terminate()
                if timeout and time.time() - start > timeout:
                    proc.terminate()
            proc.wait()
            rc = proc.returncode
        except Exception:
            logger.exception("Failed to execute VanitySearch")
            writer.abort()
            return False
        if rc != 0 or valid_lines == 0:
            writer.abort()
            if rc != 0:
                logger.error(f"VanitySearch exited with code {rc}")
            else:
                logger.info(f"No address lines emitted by VanitySearch for {addr_mode}")
            return False
        writer.close()
        logger.info(
            json.dumps(
                {
                    "event": "vanity_output_rotated",
                    "file": os.path.basename(output_path),
                    "lines": valid_lines,
                }
            )
        )
        return True

    order = [backend]
    if backend == "cuda":
        order = ["cuda", "opencl", "cpu"]
    elif backend == "opencl":
        order = ["opencl", "cpu"]
    for b in order:
        if _run_once(b):
            return True
    return False


# Expose selected device info for callers
get_selected_backend = lambda: _SELECTED_BACKEND
get_selected_device_id = lambda: _SELECTED_DEVICE_ID
get_selected_device_name = lambda: _SELECTED_DEVICE_NAME
