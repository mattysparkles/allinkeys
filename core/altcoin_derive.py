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
import threading
import multiprocessing
import io

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
    f = open(partial_path, "w", newline="", encoding="utf-8", buffering=1)
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
    except OSError as e:
        log_message(f"❌ Failed to finalize {partial_path} → {final_path}: {e}", "ERROR")
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

    try:
        platforms = cl.get_platforms()
        platform_names = [p.name for p in platforms]
        print("🌐 Detected OpenCL Platforms:", flush=True)
        for i, p in enumerate(platforms):
            print(f"  [{i}] {p.name}", flush=True)
        if LOG_LEVEL == "DEBUG":
            print(f"[DEBUG] clGetPlatformIDs -> {platform_names}", flush=True)

        devices = []
        print("🖥️  OpenCL Devices:", flush=True)
        for p_index, p in enumerate(platforms):
            for d_index, d in enumerate(p.get_devices()):
                idx = len(devices)
                devices.append((p_index, d_index, p, d))
                print(f"  [{idx}] {p.name} / {d.name}", flush=True)
        if LOG_LEVEL == "DEBUG":
            dev_info = [f"{i}: {pl.name} / {dv.name}" for i, (_, _, pl, dv) in enumerate(devices)]
            print(f"[DEBUG] clGetDeviceIDs -> {dev_info}", flush=True)

        if not devices:
            raise RuntimeError("❌ No OpenCL devices found")

        # Map unified GPU index -> OpenCL device index
        gpu_list = list_gpus()
        gpu_map = {idx: g.get("cl_index") for idx, g in enumerate(gpu_list)}

        display_index = selected[0]
        cl_index = gpu_map.get(display_index)
        if cl_index is None:
            print(f"⚠️ OpenCL mapping not found for GPU index {display_index}", flush=True)
            raise RuntimeError("No suitable OpenCL device")

        if cl_index < 0 or cl_index >= len(devices):
            print(f"⚠️ FALLBACK TO CPU — OpenCL index {cl_index} is invalid or out of bounds", flush=True)
            raise RuntimeError("Invalid OpenCL device index")

        p_idx, d_idx, platform, device = devices[cl_index]
        if LOG_LEVEL == "DEBUG":
            print(
                f"Mapped GPU index {display_index} → Platform {p_idx}, Device {d_idx} ({device.name})",
                flush=True,
            )

        context = cl.Context([device])

        if not _gpu_logged_once:
            log_message(f"🧠 Using GPU for altcoin derive on PID {os.getpid()}: {platform.name} / {device.name}")
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


def derive_addresses_gpu(hex_keys, context=None):
    """Derive addresses using the GPU if available."""

    if context is None:
        context, device = get_gpu_context_for_altcoin()
    else:
        device = context.devices[0]
    # Enable profiling so we can time kernel execution
    queue = cl.CommandQueue(context, properties=cl.command_queue_properties.PROFILING_ENABLE)

    kernel_path = os.path.join(os.path.dirname(__file__), "hash160.cl")
    if not os.path.isfile(kernel_path):
        raise FileNotFoundError(f"❌ Missing kernel file: {kernel_path}")

    with open(kernel_path, "r", encoding="utf-8") as kf:
        kernel_code = kf.read()

    try:
        program = cl.Program(context, kernel_code).build()
    except Exception as build_err:
        log_message("❌ OpenCL build failed", "ERROR")
        log_message(str(build_err), "ERROR")
        raise

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


def convert_txt_to_csv(input_txt_path, batch_id, pause_event=None, shutdown_event=None, context=None, gpu_id=None):
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

    try:
        with open(input_txt_path, "rb") as infile_raw:
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
                safe_update_dashboard_stat(f"backlog_progress.{base_name}", round(progress, 1))
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
                                results = derive_addresses_gpu(hex_batch, context)
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
                    results = derive_addresses_gpu(hex_batch, context)
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
            safe_update_dashboard_stat(f"backlog_progress.{base_name}", 100)

            safe_increment_metric("csv_created_today", 1)
            safe_increment_metric("csv_created_lifetime", 1)
            safe_update_dashboard_stat("csv_created_today", safe_get_metric("csv_created_today"))
            safe_update_dashboard_stat("csv_created_lifetime", safe_get_metric("csv_created_lifetime"))
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
                safe_increment_metric(f"addresses_generated_today.{coin}", count)
                safe_increment_metric(f"addresses_generated_lifetime.{coin}", count)

            for coin, count in per_coin.items():
                log_message(f"📈 Generated {count} {coin.upper()} addresses", "DEBUG")

            total_dur = time.perf_counter() - start_total
            log_message(
                f"[PERF] File {filename} load:{perf_stats['load']:.2f}s derive:{perf_stats['derive']:.2f}s write:{perf_stats['write']:.2f}s total:{total_dur:.2f}s",
                "DEBUG",
            )
            return rows_written

    except Exception as e:
        log_message(f"❌ Fatal error in convert_txt_to_csv: {safe_str(e)}", "ERROR")
        return 0


from core.dashboard import init_shared_metrics, register_control_events


def _convert_file_worker(txt_file, pause_event, shutdown_event, gpu_id):
    """Helper for ProcessPoolExecutor to convert a single file.

    The worker intentionally avoids touching shared ``Manager`` state. Metrics and
    dashboard updates are handled in the parent process to prevent proxy
    invalidation when workers exit.
    """
    pause_event = _unwrap_event(pause_event)
    shutdown_event = _unwrap_event(shutdown_event)
    try:
        full_path = os.path.join(VANITY_OUTPUT_DIR, txt_file)
        lock_path = full_path + ".lock"
        if os.path.exists(lock_path):
            return txt_file, 0.0, 0, "locked"
        open(lock_path, "w").close()
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
                    raise RuntimeError(f"Invalid GPU ID {gpu_id} — OpenCL index {cl_index} not available")
                device = devices[cl_index]
                context = cl.Context([device])
                device_name = f"GPU{gpu_id} {device.name}"
            except Exception as err:
                log_message(
                    f"⚠️ FALLBACK TO CPU — OpenCL device not available: {safe_str(err)}",
                    "WARNING",
                )
        log_message(
            f"[Altcoin Derive - GPU {gpu_id if gpu_id is not None else 'CPU'}] 🚀 Starting CSV derivation on {txt_file}...",
            "INFO",
        )
        start_t = time.perf_counter()
        rows = convert_txt_to_csv(full_path, batch_id, pause_event, shutdown_event, context, gpu_id)
        duration = time.perf_counter() - start_t
        return txt_file, duration, rows, None
    except Exception as e:
        return txt_file, 0.0, 0, safe_str(e)
    finally:
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass


from core.logger import initialize_logging


def convert_txt_to_csv_loop(shared_shutdown_event, shared_metrics=None, pause_event=None, log_q=None, gpu_flag=None):
    initialize_logging(log_q)
    try:
        init_shared_metrics(shared_metrics)
        set_metric("status.altcoin", "Running")
        set_metric("altcoin_files_converted", 0)
        set_metric("derived_addresses_today", 0)
        set_metric("backlog_files_completed", 0)
        from core.dashboard import set_thread_health

        set_thread_health("altcoin", True)
        register_control_events(shared_shutdown_event, pause_event, module="altcoin")
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)
    """
    Monitors VANITY_OUTPUT_DIR for .txt files and converts them to CSV using GPU derivation.
    Handles multiple files in parallel using a process pool for true concurrency.
    Terminates cleanly on shared shutdown event (e.g. Ctrl+C).
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    log_message("📦 Altcoin conversion loop (multi-process) started...", "INFO")

    processed = set()
    queued = set()
    proc_lock = threading.Lock()
    durations = []  # track per-file processing times
    # Allow at most one worker per assigned GPU
    selected_gpus = ALTCOIN_GPUS_INDEX or get_altcoin_gpu_ids()
    base_workers = len(selected_gpus) if selected_gpus else 1
    max_workers = base_workers
    log_message(f"[GPU] Using {max_workers} worker(s) for altcoin derive", "DEBUG")

    def graceful_shutdown(sig, frame):
        log_message("🛑 Ctrl+C received in altcoin conversion loop. Shutting down...", "WARNING")
        shared_shutdown_event.set()

    signal.signal(signal.SIGINT, graceful_shutdown)

    from core.dashboard import get_pause_event

    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as executor:
        futures = {}
        while not safe_event_is_set(shared_shutdown_event):
            if safe_event_is_set(get_pause_event("altcoin")):
                time.sleep(1)
                continue
            try:
                all_txt = [
                    f
                    for f in os.listdir(VANITY_OUTPUT_DIR)
                    if f.endswith(".txt") and f not in processed and f not in queued
                ]

                safe_update_dashboard_stat("backlog_files_queued", len(all_txt) + len(queued))
                log_message(
                    f"[QUEUE] workers:{len(futures)}/{max_workers} queued:{len(all_txt) + len(queued)}",
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

                effective_gpus = selected_gpus if (gpu_flag is None or gpu_flag.value) else []
                max_workers = len(effective_gpus) if effective_gpus else 1
                while all_txt and len(futures) < max_workers:
                    txt = all_txt.pop(0)
                    assigned_gpu = None
                    if effective_gpus:
                        assigned_gpu = effective_gpus[len(futures) % len(effective_gpus)]
                    log_message(
                        f"[QUEUE] Submitting {txt} to GPU {assigned_gpu if assigned_gpu is not None else 'CPU'}",
                        "DEBUG",
                    )
                    future = executor.submit(
                        _convert_file_worker,
                        txt,
                        _unwrap_event(pause_event),
                        _unwrap_event(shared_shutdown_event),
                        assigned_gpu,
                    )
                    futures[future] = txt
                    queued.add(txt)

                done = [fut for fut in futures if fut.done()]
                for fut in done:
                    txt_file, dur, rows, err = fut.result()
                    queued.discard(futures[fut])
                    if err:
                        log_message(f"❌ Failed to convert {txt_file}: {err}", "ERROR")
                    else:
                        with proc_lock:
                            processed.add(txt_file)
                        durations.append(dur)
                        safe_increment_metric("altcoin_files_converted", 1)
                        if rows:
                            safe_increment_metric("derived_addresses_today", rows)
                        safe_increment_metric("backlog_files_completed", 1)
                        safe_update_dashboard_stat(
                            f"backlog_progress.{os.path.splitext(txt_file)[0]}", 100
                        )
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
                    del futures[fut]

                safe_update_dashboard_stat(
                    "backlog_current_file", list(queued)[0] if queued else ""
                )
                if not futures and not all_txt:
                    time.sleep(3)
            except Exception as e:
                log_message(f"❌ Error in altcoin conversion loop: {safe_str(e)}", "ERROR")

    log_message("✅ Altcoin derive loop exited cleanly.", "INFO")
    set_metric("status.altcoin", "Stopped")
    try:
        from core.dashboard import set_thread_health

        set_thread_health("altcoin", False)
    except Exception:
        pass


def start_altcoin_conversion_process(shared_shutdown_event, shared_metrics=None, pause_event=None, log_q=None, gpu_flag=None):
    """
    Starts a subprocess that monitors VANITY_OUTPUT_DIR for .txt files and converts them to multi-coin CSVs.
    Gracefully shuts down on Ctrl+C or when shutdown_event is triggered.
    Intended specifically for altcoin GPU derivation workloads.
    """
    shared_shutdown_event = _unwrap_event(shared_shutdown_event)
    pause_event = _unwrap_event(pause_event)
    process = multiprocessing.Process(
        target=convert_txt_to_csv_loop,
        args=(shared_shutdown_event, shared_metrics, pause_event, log_q, gpu_flag),
        name="AltcoinConverter",
    )
    # This process launches a ``ProcessPoolExecutor`` for parallel conversions
    # and therefore cannot be a daemon.  Marking it as non-daemonic avoids
    # ``daemonic processes are not allowed to have children`` errors.
    process.daemon = False
    process.start()
    log_message("🚀 Altcoin derive subprocess started...", "INFO")
    return process


if __name__ == "__main__":
    from multiprocessing import freeze_support, Manager

    freeze_support()
    print("🧪 Running one-shot altcoin conversion test (dev mode)...", flush=True)
    mgr = Manager()
    shared_event = mgr.Event()
    from core.logger import start_listener, log_queue

    start_listener()
    try:
        start_altcoin_conversion_process(shared_event, None, shared_event, log_queue, None)
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("🛑 Ctrl+C received. Stopping...", flush=True)
        shared_event.set()
