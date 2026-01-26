import os
import re
import subprocess
import threading
import time
import tempfile
import uuid
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path

from config.settings import (
    GPU_BACKEND,
    FORCE_CPU_FALLBACK,
    VANITYSEARCH_BIN_CUDA,
    VANITYSEARCH_BIN_OPENCL,
    VANITYSEARCH_BIN_CPU,
    VANITY_ROTATE_LINES,
    VANITY_MAX_BYTES,
    VANITY_ROTATE_SECONDS,
    ENABLE_P2PKH,
    ENABLE_P2WPKH,
    ENABLE_TAPROOT,
    DEFAULT_BTC_PATTERNS,
    DEFAULT_BTC_PATTERNS_BECH32,
    DEFAULT_BTC_PATTERNS_BECH32M,
    ENABLE_BC1_DEFAULT,
    VANITY_MODE,
    OCLVANITYGEN_PATH,
    find_vanitysearch_binary,
)
from config.directories import VANITY_OUTPUT_DIR
from core.logger import get_logger
from core.dashboard import update_dashboard_stat
from core.vanity_io import ensure_dir
from core.oclvanity_runner import run_oclvanitygen

logger = get_logger(__name__)
logger.info(
    "VanitySearch output uses native -o handling (no Python-side rotation). Empty outputs are skipped."
)

# Regex used to detect valid vanity output lines in files.
ADDR_LINE_RE = re.compile(
    r"^(?:PubAddr(?:ess)?\s*:\s*)?"
    r"(1[1-9A-HJ-NP-Za-km-z]{25,34}|"
    r"3[1-9A-HJ-NP-Za-km-z]{25,34}|"
    r"bc1[0-9ac-hj-np-z]{11,71})",
    re.IGNORECASE,
)

# throttle warning frequency
_LAST_WARN: Dict[str, float] = {}

_USED_VANITY_OUTPUTS: Set[str] = set()
_OUTPUT_PATHS_LOCK = threading.Lock()


class VanityOutputParser:
    """Track output file progress in a background thread."""

    def __init__(self, output_path: Path, poll_interval: float = 0.5) -> None:
        self.output_path = output_path
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._stopped_event = threading.Event()
        self._lock = threading.Lock()
        self._offset = 0
        self._lines_seen = 0
        self._file_size = 0
        self._thread = threading.Thread(
            target=self._run,
            name=f"vanity-parser-{output_path.name}",
            daemon=True,
        )

    def start(self) -> None:
        logger.info("Parser attached to %s", self.output_path)
        self._thread.start()
        logger.info("Rotation complete; parsing resumed")

    def request_stop(self) -> None:
        self._stop_event.set()

    def wait_for_stop(self, timeout: float = 5.0) -> bool:
        return self._stopped_event.wait(timeout=timeout)

    def snapshot(self) -> Tuple[int, int, int]:
        with self._lock:
            return self._offset, self._lines_seen, self._file_size

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.output_path.exists():
                    time.sleep(self.poll_interval)
                    continue
                file_size = self.output_path.stat().st_size
                offset = self._offset
                lines_seen = self._lines_seen
                if file_size < offset:
                    offset = 0
                if file_size > offset:
                    with self.output_path.open("rb") as fh:
                        fh.seek(offset)
                        while True:
                            chunk = fh.read(1024 * 1024)
                            if not chunk:
                                break
                            lines_seen += chunk.count(b"\n")
                        offset = fh.tell()
                with self._lock:
                    self._offset = offset
                    self._lines_seen = lines_seen
                    self._file_size = file_size
            except Exception as exc:
                if self._stop_event.is_set():
                    logger.info(
                        "Parser stopping; read error ignored for %s: %s",
                        self.output_path,
                        exc,
                    )
                else:
                    logger.warning(
                        "Parser read failed for %s: %s",
                        self.output_path,
                        exc,
                    )
            time.sleep(self.poll_interval)
        self._stopped_event.set()


def _warn_once(name: str, msg: str, interval: float = 30.0) -> None:
    """Emit ``msg`` at most once per ``interval`` seconds for the given ``name``."""
    now = time.time()
    if now - _LAST_WARN.get(name, 0) >= interval:
        logger.warning(msg)
        _LAST_WARN[name] = now


def _run_binary(binary: str, args: List[str]) -> str:
    try:
        return subprocess.check_output(
            [binary] + args, stderr=subprocess.STDOUT, text=True
        )
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
        if not bin_path or not Path(bin_path).exists():
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
    if not path or not Path(path).exists():
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
        not binary or not Path(binary).exists()
    ):
        _warn_once(
            "binary_missing",
            f"GPU backend {backend} selected but binary missing. Falling back to CPU",
        )
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

    if not binary or not Path(binary).exists():
        raise FileNotFoundError("VanitySearch binary not found.")

    _SELECTED_BACKEND = backend
    _SELECTED_DEVICE_ID = device_id
    _SELECTED_DEVICE_NAME = device_name
    _SELECTED_BINARY = binary

    update_dashboard_stat(
        {
            "vanitysearch_backend": backend,
            "vanitysearch_device_name": device_name,
        }
    )
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
    device_id: Optional[int],
    backend: str,
    timeout: int = 60,
    pause_event=None,
    addr_mode: str = "p2pkh",
) -> bool:
    """Execute VanitySearch with native -o output handling only."""
    if pause_event and pause_event.is_set():
        logger.info("Keygen paused; skipping VanitySearch job")
        return False

    def _requires_cpu_fallback(vanity_pattern: str, backend_name: str) -> bool:
        if backend_name not in ("cuda", "opencl"):
            return False
        if not vanity_pattern:
            return False
        if not vanity_pattern.lower().startswith("bc1"):
            return False
        return "*" in vanity_pattern or "?" in vanity_pattern

    if backend == "oclvanitygen":
        pattern = seed_args[0] if seed_args else DEFAULT_BTC_PATTERNS[0]
        output_path = _reserve_output_path(
            str(VANITY_OUTPUT_DIR), f"vanitysearch_{addr_mode}"
        )
        return run_oclvanitygen(
            pattern,
            str(output_path),
            timeout=timeout,
            pause_event=pause_event,
            addr_mode=addr_mode,
        )

    # Ensure pattern (last element of seed_args) remains final CLI arg
    if seed_args:
        core_args, pattern = seed_args[:-1], seed_args[-1]
    else:
        core_args, pattern = [], ""

    if _requires_cpu_fallback(pattern, backend):
        _warn_once(
            "bech32_gpu_unsupported",
            "Bech32 wildcard patterns are not supported by VanitySearch GPU backend; falling back to CPU.",
        )
        backend = "cpu"
        device_id = None

    binary = resolve_vanitysearch_binary(backend)
    base_cmd = [binary] + seed_args
    logger.info(f"Base VanitySearch command: {' '.join(base_cmd)}")

    # IMPORTANT: Pass the FINAL output path directly to VanitySearch via -o.
    # Do not introduce Python-side rotation, temp files, or stdout rewriting.
    # VanitySearch must own file creation to avoid repeated regressions.
    base_args = core_args
    if backend in ("cuda", "opencl") and device_id is not None:
        base_args = base_args + ["-gpu", str(device_id)]

    output_file, rc, _rotation = run_vanitysearch_batch(
        binary=binary,
        base_args=base_args,
        output_dir=str(VANITY_OUTPUT_DIR),
        output_prefix=f"vanitysearch_{addr_mode}",
        pattern=pattern or None,
        timeout=timeout,
        pause_event=pause_event,
    )
    update_dashboard_stat("vanitysearch_addr_mode", addr_mode)
    logger.info(f"Executing VanitySearch (no stdout capture): {output_file}")
    if rc != 0 and pause_event and pause_event.is_set():
        return False

    time.sleep(1.5)

    if not output_file.exists():
        logger.info("No VanitySearch output produced for %s", addr_mode)
        return False

    total_lines = _count_matching_lines(output_file, ADDR_LINE_RE)
    if total_lines == 0:
        logger.info(
            "No address lines emitted by VanitySearch for %s; output discarded",
            addr_mode,
        )
        try:
            output_file.unlink(missing_ok=True)
        except Exception:
            logger.debug("Unable to remove empty VanitySearch output", exc_info=True)
        return False

    logger.info(
        "📄 VanitySearch wrote %s lines to %s",
        total_lines,
        output_file.name,
    )
    return True


# Expose selected device info for callers


def get_selected_backend():
    """Return the active GPU backend selected at runtime."""
    return _SELECTED_BACKEND


def get_selected_device_id():
    """Return the numeric identifier of the chosen GPU device."""
    return _SELECTED_DEVICE_ID


def get_selected_device_name():
    """Return the display name of the selected GPU device."""
    return _SELECTED_DEVICE_NAME


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
    for p in pats or []:
        if not p or not isinstance(p, str):
            continue
        if p.lower().startswith("bc1") and not ENABLE_BC1_DEFAULT:
            continue
        out.append(p.strip())
    return out


def _apply_mode_flags(args: List[str]) -> None:
    mode = (VANITY_MODE or "both").lower()
    if mode == "both":
        args.append("-b")  # both compressed & uncompressed
    elif mode == "uncompressed":
        args.append("-u")  # uncompressed only
    else:
        pass  # compressed-only (no flag)


def _reserve_output_path(output_dir: str, prefix: str) -> Path:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex
    output_path = output_root / f"{prefix}_{ts}_{unique}.txt"
    with _OUTPUT_PATHS_LOCK:
        if str(output_path) in _USED_VANITY_OUTPUTS:
            raise RuntimeError(f"VanitySearch output filename reused: {output_path}")
        if output_path.exists():
            raise RuntimeError(
                f"VanitySearch output filename already exists: {output_path}"
            )
        _USED_VANITY_OUTPUTS.add(str(output_path))
    return output_path


# INFRASTRUCTURE: DO NOT MODIFY — VANITYSEARCH CONTROLS OUTPUT ROTATION
def run_vanitysearch_batch(
    *,
    binary: str,
    base_args: List[str],
    output_dir: str,
    output_prefix: str,
    pattern: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    pause_event=None,
    stdout_setting=None,
    max_lines: Optional[int] = None,
    max_bytes: Optional[int] = None,
    max_seconds: Optional[int] = None,
) -> Tuple[Path, int, Optional[dict]]:
    """Invoke VanitySearch using native -o file output with strict safeguards."""
    if stdout_setting is not None:
        raise RuntimeError("VanitySearch stdout capture is forbidden.")

    if max_lines is None:
        max_lines = VANITY_ROTATE_LINES
    if max_bytes is None:
        max_bytes = VANITY_MAX_BYTES
    if max_seconds is None:
        max_seconds = VANITY_ROTATE_SECONDS
    if max_seconds and timeout:
        timeout = min(timeout, max_seconds)
    elif max_seconds:
        timeout = max_seconds

    output_path = _reserve_output_path(output_dir, output_prefix)
    cmd = [binary] + base_args + ["-o", str(output_path)]
    if pattern:
        cmd.append(pattern)

    logger.info(f"Launching VanitySearch (detached): {' '.join(cmd)}")
    logger.info(
        "Rotation is implemented by process restart. VanitySearch cannot rotate files."
    )
    proc = subprocess.Popen(
        cmd,
        stdout=None,
        stderr=None,
        env=env,
    )

    def _stop_parser(reason: str, rotation: bool) -> None:
        if rotation:
            logger.info("Stopping parser for rotation")
        else:
            logger.info("Stopping parser (%s)", reason)
        parser.request_stop()
        parser.wait_for_stop()
        logger.info("Parser detached from %s", output_path)

    start = time.time()
    rotation_info = None
    parser = None
    rotation_start = None
    parser_detached = False
    first_write_size = None
    warned_rotation_before_write = False
    rotation_before_write = False
    logger.info("Waiting for first output write...")
    while proc.poll() is None and first_write_size is None:
        try:
            file_size = output_path.stat().st_size
        except FileNotFoundError:
            file_size = 0
        if file_size > 0:
            first_write_size = file_size
            break
        if max_seconds and time.time() - start > max_seconds:
            if not warned_rotation_before_write:
                logger.warning(
                    "Rotation timeout hit before first output; rotating anyway to avoid stalled batches."
                )
                warned_rotation_before_write = True
            rotation_info = {
                "reason": "timeout_before_write",
                "timestamp": time.time(),
                "path": str(output_path),
            }
            rotation_before_write = True
            proc.terminate()
            break
        time.sleep(0.5)

    if rotation_before_write:
        logger.info("Rotation triggered before first output write; skipping parser attach.")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return output_path, proc.returncode or 0, rotation_info

    if first_write_size is not None:
        logger.info("First write detected (size=%s)", first_write_size)
        parser = VanityOutputParser(output_path)
        parser.start()
        logger.info("Parser attached")
        rotation_start = time.time()
        logger.info("Rotation armed")
    else:
        logger.warning("VanitySearch exited before first output write.")

    while proc.poll() is None:
        if parser is None:
            time.sleep(0.5)
            continue
        if pause_event and pause_event.is_set():
            _stop_parser("pause", rotation=False)
            parser_detached = True
            proc.terminate()
            break
        elapsed = time.time() - rotation_start if rotation_start else 0
        if timeout and rotation_start and elapsed > timeout:
            rotation_info = {
                "reason": "timeout",
                "timestamp": time.time(),
                "path": str(output_path),
            }
            _stop_parser("timeout", rotation=True)
            parser_detached = True
            proc.terminate()
            break
        _, lines_seen, file_size = parser.snapshot()
        if max_lines and lines_seen >= max_lines:
            logger.info(
                "VanitySearch batch reached %s lines; restarting for rotation.",
                max_lines,
            )
            rotation_info = {
                "reason": "lines",
                "timestamp": time.time(),
                "path": str(output_path),
            }
            _stop_parser("max_lines", rotation=True)
            parser_detached = True
            proc.terminate()
            break
        if max_bytes and file_size >= max_bytes:
            logger.info(
                "VanitySearch batch reached %s bytes; restarting for rotation.",
                max_bytes,
            )
            rotation_info = {
                "reason": "bytes",
                "timestamp": time.time(),
                "path": str(output_path),
            }
            _stop_parser("max_bytes", rotation=True)
            parser_detached = True
            proc.terminate()
            break
        time.sleep(0.5)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if parser is not None and not parser_detached:
        _stop_parser("complete", rotation=False)

    return output_path, proc.returncode or 0, rotation_info


def _warn_zero_matches(
    mode: str, pattern_count: int, used_file: bool, seed: str
) -> None:
    """Log detailed warning when a mode exits with no vanity lines."""
    logger.warning(
        f"⚠️ VanitySearch exited cleanly with 0 matches | mode={mode} | patterns={pattern_count} | "
        f"input={'-i' if used_file else 'single'} | seed={seed} | device={get_selected_device_name()}"
    )


def _count_matching_lines(path: Path, addr_re: re.Pattern[str]) -> int:
    """Count address-like lines in ``path``."""

    count = 0
    try:
        with path.open(
            "r", encoding="utf-8", errors="ignore", buffering=1024 * 1024
        ) as fh:
            for raw in fh:
                if addr_re.search(raw.strip()):
                    count += 1
    except FileNotFoundError:
        return 0
    return count


def run_vanity_generator(seed_start: int, patterns: List[str], stop_event=None) -> int:
    """
    Runs VanitySearch with correct CLI layout:
      - -b/-u are flags (no value)
      - Single prefix -> final positional arg
      - Multiple prefixes -> write to temp file and pass -i <file>
    Uses VanitySearch's built-in ``-o`` output mechanism for file creation.
    """
    out_dir = ensure_dir(VANITY_OUTPUT_DIR)
    # ``ensure_dir`` now returns a string path. Wrap with ``Path`` so ``resolve``
    # is always available while still providing the string to callers that expect
    # one. The previous direct ``out_dir.resolve()`` call triggered an
    # ``AttributeError`` once ``ensure_dir`` began normalising to ``str``,
    # preventing VanitySearch from writing any output files.
    logger.info(f"Vanity output directory: {Path(out_dir).resolve()}")
    exe = _resolve_exe()
    if not exe:
        logger.error("❌ VanitySearch binary not found.")
        return 0

    seed = _normalize_seed(seed_start)
    pats = _filter_patterns(patterns)
    if not pats:
        pats = ["1**"]  # safe default

    base = ["-s", seed, "-q"]
    _apply_mode_flags(base)
    logger.info(f"Base VanitySearch command: {' '.join([exe] + base)}")

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
            fd, tmpfile = tempfile.mkstemp(
                prefix="vanity_prefixes_", suffix=".txt", dir=out_dir
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(pats) + "\n")
            single_suffix = []
            multi_suffix = ["-i", tmpfile]

        addr_re = re.compile(
            r"^(?:PubAddr(?:ess)?\s*:\s*)?"  # optional "PubAddress:" prefix
            r"(1[1-9A-HJ-NP-Za-km-z]{25,34}|"  # legacy Base58 addresses
            r"3[1-9A-HJ-NP-Za-km-z]{25,34}|"  # P2SH addresses
            r"bc1[0-9ac-hj-np-z]{11,71})",  # Bech32 addresses
            re.IGNORECASE,
        )

        for mode_name, mode_flag in modes:
            args_base = base + mode_flag
            current_multi_suffix = list(multi_suffix)
            current_single_suffix = list(single_suffix)
            attempt_fallback = bool(current_multi_suffix)

            while True:
                args = args_base + current_multi_suffix
                try:
                    logger.info(
                        f"🧪 VanitySearch ({mode_name}) command: {' '.join(args)}"
                    )
                    final_path, rc, _rotation = run_vanitysearch_batch(
                        binary=exe,
                        base_args=args,
                        output_dir=out_dir,
                        output_prefix=f"vanity_{mode_name.lower()}",
                        pattern=current_single_suffix[0] if current_single_suffix else None,
                        pause_event=stop_event,
                    )
                except RuntimeError:
                    logger.exception("VanitySearch safeguard triggered; aborting.")
                    raise
                except Exception as e:
                    logger.warning(f"⚠️ VanitySearch {mode_name} failed: {e}")
                    try:
                        final_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    break

                total_lines = _count_matching_lines(final_path, addr_re)
                if attempt_fallback and total_lines == 0 and current_multi_suffix:
                    try:
                        final_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    logger.warning("⚠️ No output; sanity-checking with 1**")
                    current_multi_suffix = []
                    current_single_suffix = ["1**"]
                    attempt_fallback = False
                    continue
                if total_lines > 0:
                    logger.info(
                        f"✅ VanitySearch finished ({mode_name}) with {total_lines} matches."
                    )
                    return total_lines
                else:
                    try:
                        final_path.unlink(missing_ok=True)
                    except Exception:
                        logger.debug(
                            "Unable to remove empty VanitySearch output", exc_info=True
                        )
                    if rc == 0:
                        _warn_zero_matches(
                            mode_name, len(pats), bool(multi_suffix), seed
                        )
                    else:
                        logger.warning(
                            f"⚠️ VanitySearch exited rc={rc}, matches={total_lines}. Trying next mode..."
                        )
                    break

        logger.error("❌ VanitySearch produced no output in any mode.")
        return 0

    finally:
        if tmpfile:
            try:
                Path(tmpfile).unlink(missing_ok=True)
            except Exception:
                pass
