"""
core/altcoin_derive.py

Converts VanitySearch .txt files to multi-coin .csv files.
Uses GPU (OpenCL) to derive addresses for: BTC, DOGE, LTC, DASH, BCH, RVN, PEP, ETH.
Preserves all columns and GPU pipeline. Flushes rows as it writes.
"""

import signal
import sys
import os
import csv
import hashlib
import base58
import pyopencl as cl
import numpy as np
from eth_hash.auto import keccak
from ecdsa import SigningKey, SECP256k1
import time
import multiprocessing
import io
import pathlib
from queue import Empty

try:
    import psutil
except ImportError:
    psutil = None

_gpu_logged_once = False
_cpu_logged_once = False
# Flag indicating if the CPU fallback path was taken
cpu_fallback_active = False

# Prevent writing absurdly large fields to CSV
MAX_FIELD_SIZE = 10000  # 10KB per field safety cap

from config.settings import (
    ENABLE_ALTCOIN_DERIVATION,
    ENABLE_SEED_VERIFICATION,
    DOGE,
    DASH,
    LTC,
    BCH,
    RVN,
    PEP,
    ETH,
    CSV_DIR,
    VANITY_OUTPUT_DIR,
    MAX_CSV_MB,
    BCH_CASHADDR_ENABLED,
    ALTCOIN_GPUS_INDEX,
    LOG_LEVEL,
    EXCLUDE_ETH_FROM_DERIVE,
)
from core.logger import log_message
from core.dashboard import update_dashboard_stat, set_metric, get_metric, increment_metric
import core.checkpoint as checkpoint
from core.gpu_selector import get_altcoin_gpu_ids, list_gpus
import config.settings as settings
from core.utils.io_safety import safe_nonempty


def safe_str(obj):
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "<unprintable exception>"


# ---------------------------------------------------------------------------
# Safe helpers for multiprocessing primitives and Manager-proxy calls.
# These wrappers guard against ``KeyError`` or disconnection issues that can
# happen when Manager-backed objects are accessed after a worker exits.  This
# allows GPU workers to fail gracefully without crashing the parent process.
# ---------------------------------------------------------------------------


def safe_event_is_set(ev):
    """Return ``ev.is_set()`` but swallow proxy-related errors."""
    if ev is None:
        return False
    real_ev = getattr(ev, "_ev", ev)
    try:
        return real_ev.is_set()
    except (OSError, EOFError, KeyError):
        log_message("⚠️ Lost access to control event; assuming not set", "WARNING")
        return False


def _unwrap_event(ev):
    """Return the underlying ``multiprocessing.Event`` if wrapped."""
    return getattr(ev, "_ev", ev)


def safe_update_dashboard_stat(key, value=None):
    try:
        update_dashboard_stat(key, value)
    except (KeyError, EOFError):
        log_message(f"⚠️ Dashboard update failed for {key}", "WARNING")


def safe_increment_metric(key, amount=1):
    try:
        increment_metric(key, amount)
    except (KeyError, EOFError):
        log_message(f"⚠️ Metric update failed for {key}", "WARNING")


def safe_get_metric(key, default=0):
    try:
        return get_metric(key)
    except (KeyError, EOFError):
        log_message(f"⚠️ Metric read failed for {key}", "WARNING")
        return default


def get_file_size_mb(path):
    """Returns the file size in megabytes."""
    return os.path.getsize(path) / (1024 * 1024)


def open_new_csv_writer(index, base_name=None):
    """Create a new CSV writer to a temporary ``.partial.csv`` file.

    Returns a tuple ``(file_handle, csv_writer, final_path, partial_path)``.
    If the final CSV already exists, ``(None, None, final_path, None)`` is
    returned so the caller can skip processing.
    """
    os.makedirs(CSV_DIR, exist_ok=True)

    if base_name:
        final_path = os.path.join(CSV_DIR, f"{base_name}_part_{index}.csv")
    else:
        final_path = os.path.join(CSV_DIR, f"keys_batch_{index:05d}.csv")

    if os.path.exists(final_path):
        # File already finalized - skip writing a duplicate
        return None, None, final_path, None

    partial_path = final_path.replace(".csv", ".partial.csv")
    try:
        f = open(partial_path, "w", newline="", encoding="utf-8", buffering=1)
        log_message(f"Opened {partial_path} for writing", "INFO")
    except Exception as e:
        log_message(f"❌ Failed to open {partial_path}: {safe_str(e)}", "ERROR", exc_info=True)
        return None, None, final_path, None
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "original_seed",
            "hex_key",
            "btc_C",
            "btc_U",
            "ltc_C",
            "ltc_U",
            "doge_C",
            "doge_U",
            "bch_C",
            "bch_U",
            "eth",
            "dash_C",
            "dash_U",
            "rvn_C",
            "rvn_U",
            "pep_C",
            "pep_U",
            "private_key",
            "compressed_address",
            "uncompressed_address",
            "batch_id",
            "index",
        ],
    )
    writer.writeheader()
    f.flush()
    return f, writer, final_path, partial_path


def finalize_csv(partial_path, final_path):
    try:
        os.replace(partial_path, final_path)
        log_message(f"Finalized CSV {final_path}", "INFO")
    except OSError as e:
        log_message(f"❌ Failed to finalize {partial_path} → {final_path}: {e}", "ERROR", exc_info=True)
        return False
    return True


def get_compressed_pubkey(priv_bytes):
    sk = SigningKey.from_string(priv_bytes, curve=SECP256k1)
    point = sk.get_verifying_key().pubkey.point
    x = point.x()
    y = point.y()
    prefix = b"\x03" if (y & 1) else b"\x02"
    return prefix + x.to_bytes(32, "big")


def hash160_cpu(data):
    """Python implementation of HASH160 for CPU fallback paths."""
    sha = hashlib.sha256(data).digest()
    rip = hashlib.new("ripemd160", sha).digest()
    return rip


def b58(prefix, payload):
    full = prefix + payload
    checksum = hashlib.sha256(hashlib.sha256(full).digest()).digest()[:4]
    return base58.b58encode(full + checksum).decode()


def get_gpu_context_for_altcoin():
    """Return an OpenCL context for the configured altcoin GPU."""
    # Detect available platforms and map the configured GPU index to the
    # correct OpenCL device. This establishes the context used by workers for
    # kernel execution.
    global _gpu_logged_once

    selected = ALTCOIN_GPUS_INDEX
    if not selected:
        selected = get_altcoin_gpu_ids()
        if not selected:
            from core.gpu_selector import assign_gpu_roles

            assign_gpu_roles()
            selected = get_altcoin_gpu_ids()
            if not selected:
                raise RuntimeError("❌ No GPU assigned for altcoin derivation.")

    gpu_list = list_gpus()
    available_ids = {g["id"] for g in gpu_list}
    if selected and selected[0] not in available_ids:
        # Provided index is not present; warn and use the first available GPU instead.
        log_message(
            f"⚠️ GPU index {selected[0]} not found; defaulting to GPU 0",
            "WARNING",
        )
        selected = [next(iter(available_ids), 0)]

    try:
        platforms = cl.get_platforms()
        platform_names = [p.name for p in platforms]
        log_message("🌐 Detected OpenCL Platforms:", "INFO")
        for i, p in enumerate(platforms):
            log_message(f"  [{i}] {p.name}", "INFO")
        if LOG_LEVEL == "DEBUG":
            log_message(f"clGetPlatformIDs -> {platform_names}", "DEBUG")

        devices = []
        log_message("🖥️  OpenCL Devices:", "INFO")
        for p_index, p in enumerate(platforms):
            for d_index, d in enumerate(p.get_devices(device_type=cl.device_type.GPU)):
                idx = len(devices)
                devices.append((p_index, d_index, p, d))
                log_message(f"  [{idx}] {p.name} / {d.name}", "INFO")
        if LOG_LEVEL == "DEBUG":
            dev_info = [f"{i}: {pl.name} / {dv.name}" for i, (_, _, pl, dv) in enumerate(devices)]
            log_message(f"clGetDeviceIDs -> {dev_info}", "DEBUG")

        if not devices:
            raise RuntimeError("❌ No OpenCL devices found")

        # Map unified GPU index -> OpenCL device index
        gpu_list = list_gpus()
        gpu_map = {idx: g.get("cl_index") for idx, g in enumerate(gpu_list)}

        display_index = selected[0]
        cl_index = gpu_map.get(display_index)
        if cl_index is None:
            log_message(f"⚠️ OpenCL mapping not found for GPU index {display_index}", "WARNING")
            raise RuntimeError("No suitable OpenCL device")

        if cl_index < 0 or cl_index >= len(devices):
            log_message(
                f"⚠️ FALLBACK TO CPU — OpenCL index {cl_index} is invalid or out of bounds",
                "WARNING",
            )
            raise RuntimeError("Invalid OpenCL device index")

        p_idx, d_idx, platform, device = devices[cl_index]
        if LOG_LEVEL == "DEBUG":
            log_message(
                f"Mapped GPU index {display_index} → Platform {p_idx}, Device {d_idx} ({device.name})",
                "DEBUG",
            )

        log_message(
            f"Initializing OpenCL for GPU {display_index} ({device.name})",
            "DEBUG",
        )
        context = cl.Context([device])
        log_message(
            f"GPU context established for {device.vendor} {device.name} (cl index {cl_index})",
            "INFO",
        )

        if not _gpu_logged_once:
            log_message(
                f"🧠 Using GPU for altcoin derive on PID {os.getpid()}: {platform.name} / {device.name}",
                "INFO",
            )
            _gpu_logged_once = True

        return context, device
    except Exception as err:
        log_message(f"⚠️ FALLBACK TO CPU — OpenCL device not available: {safe_str(err)}", "WARNING")
        raise


# CashAddr utility
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
GENERATOR = [0x98F2BC8E61, 0x79B76D99E2, 0xF33E5FB3C4, 0xAE2EABE2A8, 0x1E4F43E470]


def polymod(values):
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07FFFFFFFF) << 5) ^ d
        for i in range(5):
            if (c0 >> i) & 1:
                c ^= GENERATOR[i]
    return c ^ 1


def prefix_expand(prefix):
    return [ord(x) & 0x1F for x in prefix] + [0]


def convertbits(data, frombits, tobits, pad=True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for b in data:
        acc = (acc << frombits) | b
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def cashaddr_encode(prefix, payload):
    data = convertbits(payload, 8, 5)
    checksum = polymod(prefix_expand(prefix) + data + [0] * 8)
    for i in range(8):
        data.append((checksum >> 5 * (7 - i)) & 0x1F)
    return prefix + ":" + "".join([CHARSET[d] for d in data])


def load_kernel_source(device):
    """Return the appropriate OpenCL kernel source based on GPU vendor."""
    name = (getattr(device, "name", "") or "").upper()
    vendor = (getattr(device, "vendor", "") or "").upper()
    use_nvidia = "NVIDIA" in name or "NVIDIA" in vendor
    kernel = "hash160_nvidia.cl" if use_nvidia else "hash160.cl"
    log_message(
        f"[Altcoin Derive] Using kernel {kernel} for device {device.name}",
        "INFO",
    )
    path = pathlib.Path(__file__).with_name(kernel)
    if not path.is_file():
        msg = f"Kernel file {kernel} not found at {path}"
        log_message(f"❌ {msg}", "ERROR")
        raise FileNotFoundError(msg)
    try:
        source = path.read_text()
        log_message(f"Loaded kernel {kernel} for {device.vendor}", "INFO")
        return source
    except Exception as e:
        log_message(f"❌ Failed to load kernel {kernel}: {safe_str(e)}", "ERROR", exc_info=True)
        raise


def derive_addresses_gpu(hex_keys, context=None):
    """Derive addresses using the GPU if available."""

    if context is None:
        context, device = get_gpu_context_for_altcoin()
    else:
        device = context.devices[0]
    # Enable profiling so we can time kernel execution
    queue = cl.CommandQueue(context, properties=cl.command_queue_properties.PROFILING_ENABLE)

    # NVIDIA cards require a slightly different kernel without AMD-specific
    # flags.  Choose the appropriate source based on the device in use.
    kernel_code = load_kernel_source(device)

    program = cl.Program(context, kernel_code)
    try:
        program.build()
        log_message(f"OpenCL kernel compiled for {device.name}", "INFO")
        # Log any compiler messages for debugging
        try:
            build_log = program.get_build_info(device, cl.program_build_info.LOG)
            if build_log.strip():
                log_message(f"Kernel build log ({device.name}): {build_log.strip()}", "DEBUG")
        except Exception:
            log_message("Failed to retrieve kernel build log", "DEBUG", exc_info=True)
    except Exception as build_err:
        # Capture and report the build log from each device to aid debugging
        for dev in context.devices:
            try:
                log = program.get_build_info(dev, cl.program_build_info.LOG)
                log_message(f"Kernel build log ({dev.name}): {log}", "ERROR")
            except Exception:
                log_message(
                    f"Failed to retrieve build log for {getattr(dev, 'name', 'unknown device')}",
                    "DEBUG",
                    exc_info=True,
                )
        log_message("❌ OpenCL build failed", "ERROR", exc_info=True)
        raise RuntimeError(f"OpenCL kernel build failed: {build_err}")

    kernel_hash160 = cl.Kernel(program, "hash160")

    key_bytes = [bytes.fromhex(k.lstrip("0x").zfill(64)) for k in hex_keys]
    count = len(key_bytes)
    log_message(f"[GPU] Deriving {count} keys (work items: {count})", "DEBUG")

    # Generate public keys on CPU
    pub_c_list = []
    pub_u_list = []
    for priv in key_bytes:
        sk = SigningKey.from_string(priv, curve=SECP256k1)
        vk = sk.get_verifying_key()
        vk_bytes = vk.to_string()
        x = vk_bytes[:32]
        y = vk_bytes[32:]
        prefix = b"\x03" if (y[-1] % 2) else b"\x02"
        pub_c_list.append(prefix + x)
        pub_u_list.append(b"\x04" + x + y)

    mf = cl.mem_flags

    comp_flat = b"".join(pub_c_list)
    uncomp_flat = b"".join(pub_u_list)

    comp_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=comp_flat)
    uncomp_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=uncomp_flat)

    out_comp_buf = cl.Buffer(context, mf.WRITE_ONLY, 20 * count)
    out_uncomp_buf = cl.Buffer(context, mf.WRITE_ONLY, 20 * count)

    start_gpu = time.perf_counter()
    kernel_hash160.set_args(comp_buf, out_comp_buf, np.uint32(33))
    event_comp = cl.enqueue_nd_range_kernel(queue, kernel_hash160, (count,), None)

    kernel_hash160.set_args(uncomp_buf, out_uncomp_buf, np.uint32(65))
    event_uncomp = cl.enqueue_nd_range_kernel(queue, kernel_hash160, (count,), None)

    hash_comp = np.empty((count, 20), dtype=np.uint8)
    hash_uncomp = np.empty((count, 20), dtype=np.uint8)
    cl.enqueue_copy(queue, hash_comp, out_comp_buf)
    cl.enqueue_copy(queue, hash_uncomp, out_uncomp_buf)
    queue.finish()

    end_gpu = time.perf_counter()
    comp_ms = (event_comp.profile.end - event_comp.profile.start) / 1e6
    uncomp_ms = (event_uncomp.profile.end - event_uncomp.profile.start) / 1e6
    log_message(
        f"[GPU] Kernel times - compressed:{comp_ms:.3f}ms uncompressed:{uncomp_ms:.3f}ms total:{end_gpu - start_gpu:.3f}s",
        "DEBUG",
    )

    results = []
    for idx in range(count):
        try:
            pubkey_compressed = pub_c_list[idx]

            hash160_c = bytes(hash_comp[idx])
            hash160_u = bytes(hash_uncomp[idx])

            result = {
                "btc_C": b58(b"\x00", hash160_c),
                "btc_U": b58(b"\x00", hash160_u),
                "ltc_C": b58(b"\x30", hash160_c),
                "ltc_U": b58(b"\x30", hash160_u),
                "doge_C": b58(b"\x1e", hash160_c),
                "doge_U": b58(b"\x1e", hash160_u),
                "dash_C": b58(b"\x4c", hash160_c),
                "dash_U": b58(b"\x4c", hash160_u),
                "bch_C": cashaddr_encode("bitcoincash", hash160_c) if BCH_CASHADDR_ENABLED else b58(b"\x00", hash160_c),
                "bch_U": cashaddr_encode("bitcoincash", hash160_u) if BCH_CASHADDR_ENABLED else b58(b"\x00", hash160_u),
                "rvn_C": b58(b"\x3c", hash160_c),
                "rvn_U": b58(b"\x3c", hash160_u),
                "pep_C": b58(b"\x37", hash160_c),
                "pep_U": b58(b"\x37", hash160_u),
            }
            if not EXCLUDE_ETH_FROM_DERIVE:
                result["eth"] = "0x" + keccak(pubkey_compressed[1:])[-20:].hex()

            results.append(result)

        except Exception as e:
            results.append({"error": str(e)})

    return results


def derive_addresses_cpu(hex_keys):
    """Derive addresses purely with Python when no GPU is available."""
    global _cpu_logged_once, cpu_fallback_active
    if not _cpu_logged_once:
        log_message(f"🧠 Using CPU for altcoin derive on PID {os.getpid()}", "WARNING")
        _cpu_logged_once = True
    if not cpu_fallback_active:
        log_message("❗ CPU fallback path triggered", "WARNING")
    cpu_fallback_active = True
    results = []
    for key in hex_keys:
        priv = bytes.fromhex(key.lstrip("0x").zfill(64))
        try:
            sk = SigningKey.from_string(priv, curve=SECP256k1)
            vk_bytes = sk.get_verifying_key().to_string()
            x = vk_bytes[:32]
            y = vk_bytes[32:]
            prefix = b"\x03" if (y[-1] % 2) else b"\x02"
            pubkey_compressed = prefix + x
            pubkey_uncompressed = b"\x04" + x + y
            hash160_c = hash160_cpu(pubkey_compressed)
            hash160_u = hash160_cpu(pubkey_uncompressed)

            result = {
                "btc_C": b58(b"\x00", hash160_c),
                "btc_U": b58(b"\x00", hash160_u),
                "ltc_C": b58(b"\x30", hash160_c),
                "ltc_U": b58(b"\x30", hash160_u),
                "doge_C": b58(b"\x1e", hash160_c),
                "doge_U": b58(b"\x1e", hash160_u),
                "dash_C": b58(b"\x4c", hash160_c),
                "dash_U": b58(b"\x4c", hash160_u),
                "bch_C": cashaddr_encode("bitcoincash", hash160_c) if BCH_CASHADDR_ENABLED else b58(b"\x00", hash160_c),
                "bch_U": cashaddr_encode("bitcoincash", hash160_u) if BCH_CASHADDR_ENABLED else b58(b"\x00", hash160_u),
                "rvn_C": b58(b"\x3c", hash160_c),
                "rvn_U": b58(b"\x3c", hash160_u),
                "pep_C": b58(b"\x37", hash160_c),
                "pep_U": b58(b"\x37", hash160_u),
            }
            if not EXCLUDE_ETH_FROM_DERIVE:
                result["eth"] = "0x" + keccak(pubkey_compressed[1:])[-20:].hex()
            results.append(result)
        except Exception as e:
            results.append({"error": str(e)})
    return results


def derive_addresses(hex_keys, context=None):
    """Try GPU derivation then fall back to CPU on failure."""
    try:
        return derive_addresses_gpu(hex_keys, context)
    except Exception as e:
        log_message(f"⚠️ GPU derive failed, falling back to CPU: {safe_str(e)}", "WARNING")
        global cpu_fallback_active
        cpu_fallback_active = True
        return derive_addresses_cpu(hex_keys)


def derive_altcoin_addresses_from_hex(hex_key, context=None):
    sanitized = hex_key.lower().replace("0x", "").zfill(64)
    results = derive_addresses([sanitized], context)
    return results[0] if results else {}


def convert_txt_to_csv(
    input_txt_path,
    batch_id,
    pause_event=None,
    shutdown_event=None,
    context=None,
    gpu_id=None,
    enable_dashboard=True,
):
    filename = os.path.basename(input_txt_path)
    base_name = os.path.splitext(filename)[0]
    if gpu_id is not None:
        base_name = f"{base_name}_gpu{gpu_id}"
    perf_stats = {"load": 0.0, "derive": 0.0, "write": 0.0}
    start_total = time.perf_counter()

    # If CSVs for this file already exist, assume conversion finished previously
    existing = [f for f in os.listdir(CSV_DIR) if f.startswith(base_name) and f.endswith(".csv")]
    if existing:
        log_message(f"ℹ️ Skipping {filename} because CSV output already exists", "INFO")
        return 0

    dash_update = safe_update_dashboard_stat if enable_dashboard else lambda *a, **k: None
    metric_inc = safe_increment_metric if enable_dashboard else lambda *a, **k: None
    metric_get = safe_get_metric if enable_dashboard else (lambda *a, **k: 0)

    try:
        with open(input_txt_path, "rb") as infile_raw:
            log_message(f"Opened {input_txt_path} for reading", "INFO")
            t_load = time.perf_counter()
            total_lines = sum(1 for _ in infile_raw)
            infile_raw.seek(0)
            perf_stats["load"] = time.perf_counter() - t_load
            log_message(
                f"[PERF] {filename}: loaded {total_lines} lines in {perf_stats['load']:.2f}s",
                "DEBUG",
            )

            def safe_lines(stream):
                for i, raw in enumerate(stream, 1):
                    try:
                        line = raw.decode("utf-8", errors="replace").replace("\ufffd", "?")
                        if "�" in line:
                            log_message(f"⚠️ Replaced invalid UTF-8 characters in line {i}", "WARNING")
                        yield line
                    except Exception as decode_err:
                        log_message(f"⚠️ Line {i} could not be decoded: {safe_str(decode_err)}", "WARNING")
                        continue

            infile = safe_lines(infile_raw)

            csv_index = 0
            f, writer, path, partial_path = open_new_csv_writer(csv_index, base_name)
            if f is None:
                log_message(f"ℹ️ Skipping {filename} because {os.path.basename(path)} already exists", "INFO")
                return 0

            rows_written = 0
            tally_keys = [
                "btc_C",
                "btc_U",
                "ltc_C",
                "ltc_U",
                "doge_C",
                "doge_U",
                "bch_C",
                "bch_U",
                "dash_C",
                "dash_U",
                "rvn_C",
                "rvn_U",
                "pep_C",
                "pep_U",
            ]
            if not EXCLUDE_ETH_FROM_DERIVE:
                tally_keys.insert(8, "eth")
            address_tally = {k: 0 for k in tally_keys}

            line_buffer = []
            hex_batch = []
            pub_map = {}
            meta_map = {}

            i = 0
            index = 0
            batch_size = 16384

            for line in infile:
                if safe_event_is_set(pause_event):
                    while safe_event_is_set(pause_event):
                        if safe_event_is_set(shutdown_event):
                            break
                        time.sleep(1)
                    if safe_event_is_set(shutdown_event):
                        break

                if safe_event_is_set(shutdown_event):
                    break

                i += 1
                progress = (i / total_lines) * 100 if total_lines else 100
                dash_update(f"backlog_progress.{base_name}", round(progress, 1))
                stripped = line.strip()
                if stripped.startswith("PubAddress:") or stripped.startswith("Pub Addr:"):
                    line_buffer = [stripped]
                elif stripped.startswith("Priv (WIF):") and line_buffer:
                    line_buffer.append(stripped)
                elif stripped.startswith("Priv (HEX):") and len(line_buffer) == 2:
                    line_buffer.append(stripped)
                    try:
                        pub = line_buffer[0].split(":", 1)[1].strip()
                        wif = line_buffer[1].split(":", 1)[1].strip().replace("p2pkh:", "").strip()
                        priv_hex = line_buffer[2].split(":", 1)[1].strip().replace("0x", "").zfill(64)
                        int_seed = int(priv_hex, 16)

                        hex_batch.append(priv_hex)
                        pub_map[priv_hex] = pub
                        meta_map[priv_hex] = (int_seed, wif, pub)

                        if len(hex_batch) >= batch_size:
                            t_der = time.perf_counter()
                            if context is not None:
                                results = derive_addresses(hex_batch, context)
                            else:
                                results = derive_addresses_cpu(hex_batch)
                            d_dur = time.perf_counter() - t_der
                            perf_stats["derive"] += d_dur
                            log_message(
                                f"[PERF] Derived {len(hex_batch)} keys in {d_dur:.2f}s",
                                "DEBUG",
                            )
                            for idx, derived in enumerate(results):
                                priv_hex = hex_batch[idx]
                                seed, wif, pub = meta_map[priv_hex]
                                btc_u = derived.get("btc_U", "")
                                btc_c = derived.get("btc_C", "")

                                if ENABLE_SEED_VERIFICATION and pub and btc_u != pub:
                                    log_message(f"⚠️ BTC mismatch: expected {pub}, got {btc_u}", "WARNING")
                                    continue

                                row = {
                                    "original_seed": seed,
                                    "hex_key": priv_hex,
                                    "btc_C": btc_c,
                                    "btc_U": btc_u,
                                    "ltc_C": derived.get("ltc_C", ""),
                                    "ltc_U": derived.get("ltc_U", ""),
                                    "doge_C": derived.get("doge_C", ""),
                                    "doge_U": derived.get("doge_U", ""),
                                    "bch_C": derived.get("bch_C", ""),
                                    "bch_U": derived.get("bch_U", ""),
                                    "dash_C": derived.get("dash_C", ""),
                                    "dash_U": derived.get("dash_U", ""),
                                    "rvn_C": derived.get("rvn_C", ""),
                                    "rvn_U": derived.get("rvn_U", ""),
                                    "pep_C": derived.get("pep_C", ""),
                                    "pep_U": derived.get("pep_U", ""),
                                    "private_key": wif,
                                    "compressed_address": "",  # leave blank
                                    "uncompressed_address": pub,  # from VanitySearch
                                    "batch_id": batch_id,
                                    "index": index,
                                }
                                if not EXCLUDE_ETH_FROM_DERIVE:
                                    row["eth"] = derived.get("eth", "")
                                for k in address_tally:
                                    if row.get(k):
                                        address_tally[k] += 1

                                if any(len(str(v)) > MAX_FIELD_SIZE for v in row.values()):
                                    log_message(
                                        f"⚠️ Skipping row due to oversized field: {row}",
                                        "WARNING",
                                    )
                                    continue

                                t_write = time.perf_counter()
                                writer.writerow(row)
                                perf_stats["write"] += time.perf_counter() - t_write
                                rows_written += 1
                                index += 1

                                if rows_written % 1000 == 0:
                                    f.flush()
                                if get_file_size_mb(partial_path) >= MAX_CSV_MB:
                                    f.close()
                                    finalize_csv(partial_path, path)
                                    csv_index += 1
                                    f, writer, path, partial_path = open_new_csv_writer(csv_index, base_name)
                                    if f is None:
                                        log_message(
                                            f"ℹ️ Skipping remaining output because {os.path.basename(path)} already exists",
                                            "INFO",
                                        )
                                        total_dur = time.perf_counter() - start_total
                                        log_message(
                                            f"[PERF] File {filename} aborted early after {total_dur:.2f}s",
                                            "DEBUG",
                                        )
                                        return rows_written

                            hex_batch.clear()
                            pub_map.clear()
                            meta_map.clear()

                    except Exception as e:
                        try:
                            log_message(f"❌ Failed to parse block at line {i}: {safe_str(e)}", "ERROR")
                        except Exception:
                            log_message(f"❌ Failed to parse block at line {i}: [unprintable exception]", "ERROR")
                    line_buffer = []
                else:
                    line_buffer = []

            # Final flush
            if hex_batch:
                t_der = time.perf_counter()
                if context is not None:
                    results = derive_addresses(hex_batch, context)
                else:
                    results = derive_addresses_cpu(hex_batch)
                d_dur = time.perf_counter() - t_der
                perf_stats["derive"] += d_dur
                log_message(f"[PERF] Derived {len(hex_batch)} keys in {d_dur:.2f}s", "DEBUG")
                for idx, derived in enumerate(results):
                    if safe_event_is_set(pause_event):
                        while safe_event_is_set(pause_event):
                            if safe_event_is_set(shutdown_event):
                                break
                            time.sleep(1)
                        if safe_event_is_set(shutdown_event):
                            break

                    if safe_event_is_set(shutdown_event):
                        break
                    priv_hex = hex_batch[idx]
                    seed, wif, pub = meta_map[priv_hex]
                    btc_u = derived.get("btc_U", "")
                    btc_c = derived.get("btc_C", "")

                    if ENABLE_SEED_VERIFICATION and pub and btc_u != pub:
                        log_message(f"⚠️ BTC mismatch: expected {pub}, got {btc_u}", "WARNING")
                        continue

                    row = {
                        "original_seed": seed,
                        "hex_key": priv_hex,
                        "btc_C": btc_c,
                        "btc_U": btc_u,
                        "ltc_C": derived.get("ltc_C", ""),
                        "ltc_U": derived.get("ltc_U", ""),
                        "doge_C": derived.get("doge_C", ""),
                        "doge_U": derived.get("doge_U", ""),
                        "bch_C": derived.get("bch_C", ""),
                        "bch_U": derived.get("bch_U", ""),
                        "dash_C": derived.get("dash_C", ""),
                        "dash_U": derived.get("dash_U", ""),
                        "rvn_C": derived.get("rvn_C", ""),
                        "rvn_U": derived.get("rvn_U", ""),
                        "pep_C": derived.get("pep_C", ""),
                        "pep_U": derived.get("pep_U", ""),
                        "private_key": wif,
                        "compressed_address": "",  # leave blank
                        "uncompressed_address": pub,  # from VanitySearch
                        "batch_id": batch_id,
                        "index": index,
                    }
                    if not EXCLUDE_ETH_FROM_DERIVE:
                        row["eth"] = derived.get("eth", "")
                    for k in address_tally:
                        if row.get(k):
                            address_tally[k] += 1

                    if any(len(str(v)) > MAX_FIELD_SIZE for v in row.values()):
                        log_message(
                            f"⚠️ Skipping row due to oversized field: {row}",
                            "WARNING",
                        )
                        continue

                    t_write = time.perf_counter()
                    writer.writerow(row)
                    perf_stats["write"] += time.perf_counter() - t_write
                    rows_written += 1
                    index += 1
                f.flush()
            f.close()
            finalize_csv(partial_path, path)
            dash_update(f"backlog_progress.{base_name}", 100)

            metric_inc("csv_created_today", 1)
            metric_inc("csv_created_lifetime", 1)
            dash_update("csv_created_today", metric_get("csv_created_today"))
            dash_update("csv_created_lifetime", metric_get("csv_created_lifetime"))
            log_message(f"✅ {os.path.basename(path)} written ({rows_written} rows)", "INFO")
            coin_map = {
                "btc_U": "btc",
                "btc_C": "btc",
                "ltc_U": "ltc",
                "ltc_C": "ltc",
                "doge_U": "doge",
                "doge_C": "doge",
                "bch_U": "bch",
                "bch_C": "bch",
                "dash_U": "dash",
                "dash_C": "dash",
                "rvn_U": "rvn",
                "rvn_C": "rvn",
                "pep_U": "pep",
                "pep_C": "pep",
            }
            if not EXCLUDE_ETH_FROM_DERIVE:
                coin_map["eth"] = "eth"
            per_coin = {c: 0 for c in coin_map.values()}
            for key, count in address_tally.items():
                coin = coin_map.get(key)
                if coin:
                    per_coin[coin] += count
                log_message(f"🔢 {key.upper()}: {count}", "DEBUG")

            for coin, count in per_coin.items():
                metric_inc(f"addresses_generated_today.{coin}", count)
                metric_inc(f"addresses_generated_lifetime.{coin}", count)

            for coin, count in per_coin.items():
                log_message(f"📈 Generated {count} {coin.upper()} addresses", "DEBUG")

            total_dur = time.perf_counter() - start_total
            log_message(
                f"[PERF] File {filename} load:{perf_stats['load']:.2f}s derive:{perf_stats['derive']:.2f}s write:{perf_stats['write']:.2f}s total:{total_dur:.2f}s",
                "DEBUG",
            )
            return rows_written

    except Exception as e:
        log_message(f"❌ Fatal error in convert_txt_to_csv: {safe_str(e)}", "ERROR", exc_info=True)
        return 0


from core.dashboard import init_shared_metrics, register_control_events


def _convert_file_worker(txt_file, pause_event, shutdown_event, gpu_id, result_q):
    """Worker process that converts a single file on one GPU.

    Results are communicated back to the parent through ``result_q`` to avoid
    ``multiprocessing.Manager`` proxies that previously triggered ``KeyError``
    when workers exited.
    """
    pause_event = _unwrap_event(pause_event)
    shutdown_event = _unwrap_event(shutdown_event)
    try:
        full_path = os.path.join(VANITY_OUTPUT_DIR, txt_file)
        lock_path = full_path + ".lock"
        if os.path.exists(lock_path):
            result_q.put((txt_file, 0.0, 0, "locked", gpu_id))
            return
        try:
            open(lock_path, "w").close()
        except Exception as e:
            log_message(f"❌ Could not create lock for {txt_file}: {safe_str(e)}", "ERROR", exc_info=True)
            result_q.put((txt_file, 0.0, 0, "lock-fail", gpu_id))
            return
        batch_id = None
        context = None
        device_name = "CPU"
        if gpu_id is not None:
            try:
                gpu_list = list_gpus()
                gpu_map = {g["id"]: g.get("cl_index") for g in gpu_list}
                cl_index = gpu_map.get(gpu_id)
                platforms = cl.get_platforms()
                devices = [d for p in platforms for d in p.get_devices()]
                if cl_index is None or cl_index >= len(devices):
                    raise RuntimeError(
                        f"Invalid GPU ID {gpu_id} — OpenCL index {cl_index} not available"
                    )
                device = devices[cl_index]
                log_message(
                    f"Initializing OpenCL for GPU {gpu_id} ({device.name})",
                    "DEBUG",
                )
                context = cl.Context([device])
                device_name = f"GPU{gpu_id} {device.name}"
                log_message(
                    f"GPU worker context established for {device.vendor} {device.name}",
                    "INFO",
                )
            except Exception as err:
                log_message(
                    f"⚠️ FALLBACK TO CPU — OpenCL device not available: {safe_str(err)}",
                    "WARNING",
                )
        log_message(
            f"[Altcoin Derive - GPU {gpu_id if gpu_id is not None else 'CPU'}] 🚀 Starting CSV derivation on {txt_file} (PID {os.getpid()})...",
            "INFO",
        )
        start_t = time.perf_counter()
        rows = convert_txt_to_csv(
            full_path,
            batch_id,
            pause_event,
            shutdown_event,
            context,
            gpu_id,
            enable_dashboard=False,
        )
        duration = time.perf_counter() - start_t
        log_message(
            f"Derivation complete for file {txt_file} using GPU {gpu_id if gpu_id is not None else 'CPU'}",
            "INFO",
        )
        # Include gpu_id so the parent can manage per-GPU queues safely
        result_q.put((txt_file, duration, rows, None, gpu_id))
    except Exception as e:
        log_message(f"❌ Worker failed for {txt_file}: {safe_str(e)}", "ERROR", exc_info=True)
        result_q.put((txt_file, 0.0, 0, safe_str(e), gpu_id))
    finally:
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            log_message("Failed to remove lock file after worker completion", "DEBUG", exc_info=True)


from core.logger import initialize_logging


def convert_txt_to_csv_loop(shared_shutdown_event, shared_metrics=None, pause_event=None, log_q=None, gpu_flag=None):
    initialize_logging(log_q)
    try:
        init_shared_metrics(shared_metrics)
        set_metric("status.altcoin", "Running")
        set_metric("altcoin_files_converted", 0)
        set_metric("derived_addresses_today", 0)
        set_metric("backlog_files_completed", 0)
        set_metric("backlog_progress", {})
        from core.dashboard import set_thread_health

        set_thread_health("altcoin", True)
        register_control_events(shared_shutdown_event, pause_event, module="altcoin")
        log_message(f"Shared metrics initialized for {__name__}", "DEBUG")
    except Exception as e:
        log_message(f"init_shared_metrics failed in {__name__}: {safe_str(e)}", "ERROR", exc_info=True)
    """
    Monitors ``VANITY_OUTPUT_DIR`` for ``.txt`` files and converts them to CSV
    using GPU derivation.  Each GPU gets its own worker process and writes to a
    dedicated CSV so no shared state or file handles are used between workers.
    ``multiprocessing.Manager`` proxies are intentionally avoided to prevent
    ``KeyError`` crashes when workers exit.
    """

    log_message("📦 Altcoin conversion loop (multi-process) started...", "INFO")
    if getattr(settings, "BTC_ONLY_MODE", False):
        log_message("BTC-only mode active; skipping altcoin derive loop.", "INFO")
        return

    processed = set()
    queued = set()
    durations = []
    result_q = multiprocessing.Queue()

    # Prefer runtime GPU assignments, falling back to static config only if none
    selected_gpus = get_altcoin_gpu_ids() or ALTCOIN_GPUS_INDEX
    gpu_list = list_gpus()
    available_ids = [g["id"] for g in gpu_list]

    if selected_gpus:
        valid = [gid for gid in selected_gpus if gid in available_ids]
        invalid = [gid for gid in selected_gpus if gid not in available_ids]
        if invalid:
            log_message(
                f"⚠️ Invalid GPU index(es) {invalid} specified for altcoin derive; ignoring",
                "WARNING",
            )
        selected_gpus = valid
    else:
        selected_gpus = available_ids

    gpu_ids_all = selected_gpus if selected_gpus else [None]
    processes = {gid: None for gid in gpu_ids_all}
    gpu_queues = {gid: [] for gid in gpu_ids_all}

    log_message(
        f"[GPU] Using {len(gpu_ids_all)} worker(s) for altcoin derive: {gpu_ids_all}",
        "DEBUG",
    )

    def graceful_shutdown(sig, frame):
        log_message(
            "🛑 Ctrl+C received in altcoin conversion loop. Shutting down...",
            "WARNING",
        )
        shared_shutdown_event.set()

    signal.signal(signal.SIGINT, graceful_shutdown)

    from core.dashboard import get_pause_event

    pause_logged = False

    while not safe_event_is_set(shared_shutdown_event):
        if safe_event_is_set(get_pause_event("altcoin")):
            if not pause_logged:
                log_message("⏸️ Altcoin derive paused. Waiting to resume...", "INFO")
                pause_logged = True
            time.sleep(1)
            continue
        elif pause_logged:
            log_message("▶️ Altcoin derive resumed.", "INFO")
            pause_logged = False
        try:
            all_txt = [
                f
                for f in os.listdir(VANITY_OUTPUT_DIR)
                if f.endswith(".txt")
                and f not in processed
                and f not in queued
                and ".tmp-" not in f
                and safe_nonempty(os.path.join(VANITY_OUTPUT_DIR, f))
            ]

            safe_update_dashboard_stat("backlog_files_queued", len(all_txt) + len(queued))
            log_message(
                f"[QUEUE] workers:{sum(1 for p in processes.values() if p)} queued:{len(all_txt) + len(queued)}",
                "DEBUG",
            )
            if psutil:
                proc = psutil.Process()
                mem_mb = proc.memory_info().rss / (1024 * 1024)
                log_message(f"[MEM] RSS {mem_mb:.1f} MB", "DEBUG")
            if durations:
                avg = sum(durations) / len(durations)
                eta_sec = avg * (len(all_txt) + len(queued))
                hrs = int(eta_sec // 3600)
                mins = int((eta_sec % 3600) // 60)
                secs = int(eta_sec % 60)
                safe_update_dashboard_stat(
                    {
                        "backlog_avg_time": f"{avg:.2f}s",
                        "backlog_eta": f"{hrs:02}:{mins:02}:{secs:02}",
                    }
                )

            effective_gpus = gpu_ids_all if (gpu_flag is None or gpu_flag.value) else []
            if not effective_gpus:
                time.sleep(3)
                continue

            # Fill per-GPU queues with pending files
            for gid in effective_gpus:
                if not gpu_queues[gid] and all_txt:
                    gpu_queues[gid].append(all_txt.pop(0))

            # Launch a dedicated subprocess per GPU so each device works in parallel
            # without sharing file handles or GPU contexts.
            for gid in effective_gpus:
                if processes[gid] is None and gpu_queues[gid]:
                    txt = gpu_queues[gid].pop(0)
                    log_message(
                        f"[QUEUE] Launching {txt} on GPU {gid if gid is not None else 'CPU'}",
                        "DEBUG",
                    )
                    p = multiprocessing.Process(
                        target=_convert_file_worker,
                        args=(
                            txt,
                            _unwrap_event(pause_event),
                            _unwrap_event(shared_shutdown_event),
                            gid,
                            result_q,
                        ),
                        name=f"AltcoinWorker-{txt}",
                    )
                    p.daemon = True
                    p.start()
                    log_message(
                        f"Spawned altcoin worker PID {p.pid} for {txt} on GPU {gid if gid is not None else 'CPU'}",
                        "INFO",
                    )
                    processes[gid] = p
                    queued.add(txt)

            try:
                while True:
                    txt_file, dur, rows, err, gid = result_q.get_nowait()
                    queued.discard(txt_file)
                    base = os.path.splitext(txt_file)[0]
                    if gid is not None:
                        base = f"{base}_gpu{gid}"
                    progress = dict(safe_get_metric("backlog_progress", {}))
                    if base in progress:
                        progress.pop(base, None)
                        safe_update_dashboard_stat("backlog_progress", progress)
                    if err:
                        log_message(f"❌ Failed to convert {txt_file}: {err}", "ERROR")
                    else:
                        processed.add(txt_file)
                        durations.append(dur)
                        safe_increment_metric("altcoin_files_converted", 1)
                        if rows:
                            safe_increment_metric("derived_addresses_today", rows)
                        safe_increment_metric("backlog_files_completed", 1)
                        if dur > 0:
                            rps = rows / dur
                            log_message(
                                f"[STATS] {txt_file} → {rows} rows in {dur:.2f}s ({rps:.1f} rows/s)",
                                "DEBUG",
                            )
                        if len(durations) % 10 == 0:
                            avg_dur = sum(durations) / len(durations)
                            log_message(
                                f"[STATS] Avg time per file: {avg_dur:.2f}s over {len(durations)} files",
                                "DEBUG",
                            )
                    # Clean up the finished process for this GPU
                    proc = processes.get(gid)
                    if proc:
                        proc.join()
                        processes[gid] = None
            except Empty:
                pass

            # Remove any dead processes that didn't report back
            for gid, proc in list(processes.items()):
                if proc and not proc.is_alive():
                    proc.join()
                    processes[gid] = None

            safe_update_dashboard_stat(
                "backlog_current_file", next(iter(queued), "")
            )
            if not any(processes.values()) and not all_txt:
                time.sleep(3)
        except Exception as e:
            log_message(f"❌ Error in altcoin conversion loop: {safe_str(e)}", "ERROR", exc_info=True)

    log_message("✅ Altcoin derive loop exited cleanly.", "INFO")
    set_metric("status.altcoin", "Stopped")
    try:
        from core.dashboard import set_thread_health

        set_thread_health("altcoin", False)
    except Exception:
        log_message("Failed to update altcoin thread health", "WARNING", exc_info=True)


def start_altcoin_conversion_process(shared_shutdown_event, shared_metrics=None, pause_event=None, log_q=None, gpu_flag=None):
    """
    Starts a subprocess that monitors VANITY_OUTPUT_DIR for .txt files and converts them to multi-coin CSVs.
    Gracefully shuts down on Ctrl+C or when shutdown_event is triggered.
    Intended specifically for altcoin GPU derivation workloads.
    """
    shared_shutdown_event = _unwrap_event(shared_shutdown_event)
    pause_event = _unwrap_event(pause_event)
    proc_args = (shared_shutdown_event, shared_metrics, pause_event, log_q, gpu_flag)
    process = multiprocessing.Process(
        target=convert_txt_to_csv_loop,
        args=proc_args,
        name="AltcoinConverter",
    )
    # This process spawns worker ``Process`` instances for parallel conversions
    # and therefore cannot be a daemon. Marking it as non-daemonic avoids
    # ``daemonic processes are not allowed to have children`` errors.
    process.daemon = False
    process.start()
    log_message(
        f"🚀 Altcoin derive subprocess PID {process.pid} started with args {proc_args}",
        "INFO",
    )
    return process


if __name__ == "__main__":
    from multiprocessing import freeze_support, Event

    freeze_support()
    print("🧪 Running one-shot altcoin conversion test (dev mode)...", flush=True)
    shared_event = Event()
    from core.logger import start_listener, log_queue

    start_listener()
    try:
        start_altcoin_conversion_process(shared_event, None, shared_event, log_queue, None)
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("🛑 Ctrl+C received. Stopping...", flush=True)
        shared_event.set()
