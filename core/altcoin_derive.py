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

_gpu_logged_once = False

from config.settings import (
    ENABLE_ALTCOIN_DERIVATION,
    ENABLE_SEED_VERIFICATION,
    DOGE, DASH, LTC, BCH, RVN, PEP, ETH,
    CSV_DIR, VANITY_OUTPUT_DIR, 
    MAX_CSV_MB, BCH_CASHADDR_ENABLED,
)
from core.logger import log_message
from core.dashboard import update_dashboard_stat, set_metric, get_metric, increment_metric
import core.checkpoint as checkpoint
from core.gpu_selector import get_altcoin_gpu_ids

def safe_str(obj):
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "<unprintable exception>"
                     
def get_file_size_mb(path):
    """Returns the file size in megabytes."""
    return os.path.getsize(path) / (1024 * 1024)


def open_new_csv_writer(index):
    """
    Opens a new CSV writer in a batch-named subfolder with appropriate headers.
    Returns: (file_handle, csv_writer, full_path)
    """
    # CSVs are now written directly to CSV_DIR to simplify downstream checks
    os.makedirs(CSV_DIR, exist_ok=True)
    path = os.path.join(CSV_DIR, f"keys_batch_{index:05d}.csv")
    f = open(path, "w", newline='', encoding="utf-8", buffering=1)
    writer = csv.DictWriter(f, fieldnames=[
        "original_seed", "hex_key", "btc_C", "btc_U", "ltc_C", "ltc_U",
        "doge_C", "doge_U", "bch_C", "bch_U", "eth", "dash_C", "dash_U",
        "rvn_C", "rvn_U", "pep_C", "pep_U",
        "private_key", "compressed_address", "uncompressed_address",
        "batch_id", "index"
    ])
    writer.writeheader()
    return f, writer, path

def get_compressed_pubkey(priv_bytes):
    sk = SigningKey.from_string(priv_bytes, curve=SECP256k1)
    point = sk.get_verifying_key().pubkey.point
    x = point.x()
    y = point.y()
    prefix = b"\x03" if (y & 1) else b"\x02"
    return prefix + x.to_bytes(32, "big")


def hash160(data):
    sha = hashlib.sha256(data).digest()
    rip = hashlib.new('ripemd160', sha).digest()
    return rip


def b58(prefix, payload):
    full = prefix + payload
    checksum = hashlib.sha256(hashlib.sha256(full).digest()).digest()[:4]
    return base58.b58encode(full + checksum).decode()


def get_gpu_context_for_altcoin():
    """
    Returns an OpenCL context and device for the assigned altcoin GPU.
    Only logs the selected GPU once per session to avoid log spam.
    """
    global _gpu_logged_once

    selected_ids = get_altcoin_gpu_ids()
    if not selected_ids:
        from core.gpu_selector import assign_gpu_roles
        assign_gpu_roles()
        selected_ids = get_altcoin_gpu_ids()
        if not selected_ids:
            raise RuntimeError("❌ No GPU assigned for altcoin derivation.")

    platforms = cl.get_platforms()
    all_devices = []
    device_lookup = {}

    device_counter = 0
    for platform in platforms:
        for device in platform.get_devices():
            all_devices.append(device)
            device_lookup[device_counter] = device
            device_counter += 1

    from core.gpu_selector import list_gpus
    available = list_gpus()
    altcoin_gpu = next((g for g in available if g["id"] == selected_ids[0]), None)

    if not altcoin_gpu or altcoin_gpu["cl_index"] is None:
        raise RuntimeError(f"❌ Could not find OpenCL index for GPU ID {selected_ids[0]}.")

    cl_index = altcoin_gpu["cl_index"]
    if cl_index >= len(all_devices):
        raise RuntimeError(f"❌ OpenCL index {cl_index} is out of bounds.")

    device = all_devices[cl_index]
    context = cl.Context([device])

    if not _gpu_logged_once:
        log_message(f"🧠 Using GPU for altcoin derive: {device.name}", "INFO")
        _gpu_logged_once = True

    return context, device

# CashAddr utility
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
GENERATOR = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4,
             0xae2eabe2a8, 0x1e4f43e470]

def polymod(values):
    c = 1
    for d in values:
        c0 = c >> 35
        c = ((c & 0x07ffffffff) << 5) ^ d
        for i in range(5):
            if ((c0 >> i) & 1):
                c ^= GENERATOR[i]
    return c ^ 1

def prefix_expand(prefix):
    return [ord(x) & 0x1f for x in prefix] + [0]

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
    checksum = polymod(prefix_expand(prefix) + data + [0]*8)
    for i in range(8):
        data.append((checksum >> 5*(7-i)) & 0x1f)
    return prefix + ':' + ''.join([CHARSET[d] for d in data])


def derive_addresses_gpu(hex_keys):
    """
    Given a list of 64-character hex private keys, uses the GPU to run SHA-256 kernel (for future compatibility),
    but derives Bitcoin and altcoin addresses from the **original key** (not the GPU-hashed output).
    """

    context, device = get_gpu_context_for_altcoin()
    queue = cl.CommandQueue(context)

    kernel_path = os.path.join(os.path.dirname(__file__), "sha256_kernel.cl")
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

    key_bytes = [bytes.fromhex(k.lstrip("0x").zfill(64)) for k in hex_keys]
    all_keys_flat = b''.join(key_bytes)
    count = len(key_bytes)

    mf = cl.mem_flags
    private_keys_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=all_keys_flat)
    output_buf = cl.Buffer(context, mf.WRITE_ONLY, 32 * count)

    INPUT_SIZE = 32  # SHA-256 input size in bytes
    program.derive_addresses(queue, (count,), None, private_keys_buf, output_buf, np.int32(INPUT_SIZE))

    # Copy output anyway for consistency / future GPU validation purposes
    derived_data = np.empty((count, 32), dtype=np.uint8)
    cl.enqueue_copy(queue, derived_data, output_buf)
    queue.finish()

    results = []
    for idx, raw in enumerate(derived_data):
        priv = key_bytes[idx]

        try:
            sk = SigningKey.from_string(priv, curve=SECP256k1)
            vk_bytes = sk.get_verifying_key().to_string()  # 64 bytes: X (32) + Y (32)

            x = vk_bytes[:32]
            y = vk_bytes[32:]

            # Compressed pubkey (33 bytes): 0x02 or 0x03 + x
            prefix = b'\x03' if (y[-1] % 2) else b'\x02'
            pubkey_compressed = prefix + x

            # Uncompressed pubkey (65 bytes): 0x04 + x + y
            pubkey_uncompressed = b'\x04' + x + y

            # Hash160 of pubkeys
            hash160_c = hash160(pubkey_compressed)
            hash160_u = hash160(pubkey_uncompressed)

            result = {
                "btc_C": b58(b'\x00', hash160_c),
                "btc_U": b58(b'\x00', hash160_u),
                "ltc_C": b58(b'\x30', hash160_c),
                "ltc_U": b58(b'\x30', hash160_u),
                "doge_C": b58(b'\x1e', hash160_c),
                "doge_U": b58(b'\x1e', hash160_u),
                "dash_C": b58(b'\x4c', hash160_c),
                "dash_U": b58(b'\x4c', hash160_u),
                "bch_C": cashaddr_encode("bitcoincash", hash160_c) if BCH_CASHADDR_ENABLED else b58(b'\x00', hash160_c),
                "bch_U": cashaddr_encode("bitcoincash", hash160_u) if BCH_CASHADDR_ENABLED else b58(b'\x00', hash160_u),
                "rvn_C": b58(b'\x3c', hash160_c),
                "rvn_U": b58(b'\x3c', hash160_u),
                "pep_C": b58(b'\x37', hash160_c),
                "pep_U": b58(b'\x37', hash160_u),
                "eth": "0x" + keccak(pubkey_compressed[1:])[-20:].hex()
            }

            results.append(result)

        except Exception as e:
            results.append({"error": str(e)})

    return results

def derive_altcoin_addresses_from_hex(hex_key):
    sanitized = hex_key.lower().replace("0x", "").zfill(64)
    results = derive_addresses_gpu([sanitized])
    return results[0] if results else {}


def convert_txt_to_csv(input_txt_path, batch_id):
    filename = os.path.basename(input_txt_path)
    base_name = os.path.splitext(filename)[0]

    try:
        with open(input_txt_path, "rb") as infile_raw:
            def safe_lines(stream):
                for i, raw in enumerate(stream, 1):
                    try:
                        line = raw.decode("utf-8", errors="replace").replace('\ufffd', '?')
                        if '�' in line:
                            log_message(f"⚠️ Replaced invalid UTF-8 characters in line {i}", "WARNING")
                        yield line
                    except Exception as decode_err:
                        log_message(f"⚠️ Line {i} could not be decoded: {safe_str(decode_err)}", "WARNING")
                        continue

            infile = safe_lines(infile_raw)

            csv_index = len([
                f for _, _, files in os.walk(CSV_DIR) for f in files if f.endswith(".csv")
            ])
            f, writer, path = open_new_csv_writer(csv_index)

            rows_written = 0
            address_tally = {k: 0 for k in [
                "btc_C", "btc_U", "ltc_C", "ltc_U", "doge_C", "doge_U",
                "bch_C", "bch_U", "eth", "dash_C", "dash_U", "rvn_C", "rvn_U", "pep_C", "pep_U"
            ]}

            line_buffer = []
            hex_batch = []
            pub_map = {}
            meta_map = {}

            i = 0
            index = 0
            batch_size = 16384

            for line in infile:
                i += 1
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
                            results = derive_addresses_gpu(hex_batch)
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
                                    "eth": derived.get("eth", ""),
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
                                    "index": index
                                }
                                for k in address_tally:
                                    if row.get(k):
                                        address_tally[k] += 1

                                writer.writerow(row)
                                rows_written += 1
                                index += 1

                                if rows_written % 100 == 0:
                                    f.flush()
                                if get_file_size_mb(path) >= MAX_CSV_MB:
                                    f.close()
                                    csv_index += 1
                                    f, writer, path = open_new_csv_writer(csv_index)

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
                results = derive_addresses_gpu(hex_batch)
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
                        "eth": derived.get("eth", ""),
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
                        "index": index
                    }
                    for k in address_tally:
                        if row.get(k):
                            address_tally[k] += 1

                    writer.writerow(row)
                    rows_written += 1
                    index += 1
                f.flush()
            f.close()

            increment_metric("csv_created_today", 1)
            increment_metric("csv_created_lifetime", 1)
            from core.dashboard import update_dashboard_stat, get_metric
            update_dashboard_stat("csv_created_today", get_metric("csv_created_today"))
            update_dashboard_stat("csv_created_lifetime", get_metric("csv_created_lifetime"))
            log_message(f"✅ {os.path.basename(path)} written ({rows_written} rows)", "INFO")
            coin_map = {
                "btc_U": "btc", "btc_C": "btc", "ltc_U": "ltc", "ltc_C": "ltc",
                "doge_U": "doge", "doge_C": "doge", "bch_U": "bch", "bch_C": "bch",
                "dash_U": "dash", "dash_C": "dash", "rvn_U": "rvn", "rvn_C": "rvn",
                "pep_U": "pep", "pep_C": "pep", "eth": "eth"
            }
            per_coin = {c: 0 for c in coin_map.values()}
            for key, count in address_tally.items():
                coin = coin_map.get(key)
                if coin:
                    per_coin[coin] += count
                log_message(f"🔢 {key.upper()}: {count}", "DEBUG")

            for coin, count in per_coin.items():
                increment_metric(f"addresses_generated_today.{coin}", count)
                increment_metric(f"addresses_generated_lifetime.{coin}", count)

            for coin, count in per_coin.items():
                log_message(f"📈 Generated {count} {coin.upper()} addresses", "DEBUG")

            return rows_written

    except Exception as e:
        log_message(f"❌ Fatal error in convert_txt_to_csv: {safe_str(e)}", "ERROR")
        return 0

from core.dashboard import init_shared_metrics, register_control_events


def convert_txt_to_csv_loop(shared_shutdown_event, shared_metrics=None, pause_event=None):
    try:
        init_shared_metrics(shared_metrics)
        set_metric("status.altcoin", True)
        set_metric("altcoin_files_converted", 0)
        set_metric("derived_addresses_today", 0)
        set_metric("backlog_files_completed", 0)
        from core.dashboard import set_thread_health
        set_thread_health("altcoin", True)
        register_control_events(shared_shutdown_event, pause_event)
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
    proc_lock = threading.Lock()
    durations = []  # track per-file processing times
    max_workers = 6  # ⚠️ Tune this based on GPU memory and throughput

    def handle_file(txt_file):
        if shared_shutdown_event.is_set():
            return
        try:
            full_path = os.path.join(VANITY_OUTPUT_DIR, txt_file)
            batch_id = None
            update_dashboard_stat("backlog_current_file", txt_file)
            start_t = time.perf_counter()
            rows = convert_txt_to_csv(full_path, batch_id)
            increment_metric("altcoin_files_converted", 1)
            if rows:
                increment_metric("derived_addresses_today", rows)
            increment_metric("backlog_files_completed", 1)
            durations.append(time.perf_counter() - start_t)
            with proc_lock:
                processed.add(txt_file)
        except Exception as e:
            log_message(f"❌ Failed to convert {txt_file}: {safe_str(e)}", "ERROR")

    def graceful_shutdown(sig, frame):
        log_message("🛑 Ctrl+C received in altcoin conversion loop. Shutting down...", "WARNING")
        shared_shutdown_event.set()

    signal.signal(signal.SIGINT, graceful_shutdown)

    from core.dashboard import get_pause_event
    while not shared_shutdown_event.is_set():
        if get_metric("global_run_state") == "paused" or (get_pause_event() and get_pause_event().is_set()):
            time.sleep(1)
            continue
        try:
            all_txt = [
                f for f in os.listdir(VANITY_OUTPUT_DIR)
                if f.endswith(".txt") and f not in processed
            ]

            update_dashboard_stat("backlog_files_queued", len(all_txt))
            if durations:
                avg = sum(durations) / len(durations)
                eta_sec = avg * len(all_txt)
                hrs = int(eta_sec // 3600)
                mins = int((eta_sec % 3600) // 60)
                secs = int(eta_sec % 60)
                update_dashboard_stat({
                    "backlog_avg_time": f"{avg:.2f}s",
                    "backlog_eta": f"{hrs:02}:{mins:02}:{secs:02}",
                })

            if not all_txt:
                time.sleep(3)
                continue

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(handle_file, f): f for f in all_txt}
                for future in as_completed(futures):
                    if shared_shutdown_event.is_set():
                        break
            update_dashboard_stat("backlog_current_file", "")
        except Exception as e:
            log_message(f"❌ Error in altcoin conversion loop: {safe_str(e)}", "ERROR")

    log_message("✅ Altcoin derive loop exited cleanly.", "INFO")
    set_metric("status.altcoin", False)
    try:
        from core.dashboard import set_thread_health
        set_thread_health("altcoin", False)
    except Exception:
        pass

def start_altcoin_conversion_process(shared_shutdown_event, shared_metrics=None, pause_event=None):
    """
    Starts a subprocess that monitors VANITY_OUTPUT_DIR for .txt files and converts them to multi-coin CSVs.
    Gracefully shuts down on Ctrl+C or when shutdown_event is triggered.
    Intended specifically for altcoin GPU derivation workloads.
    """
    process = multiprocessing.Process(
        target=convert_txt_to_csv_loop,
        args=(shared_shutdown_event, shared_metrics, pause_event),
        name="AltcoinConverter"
    )
    # This process launches a ``ProcessPoolExecutor`` for parallel conversions
    # and therefore cannot be a daemon.  Marking it as non-daemonic avoids
    # ``daemonic processes are not allowed to have children`` errors.
    process.daemon = False
    process.start()
    log_message("🚀 Altcoin derive subprocess started...", "INFO")
    return process
    
if __name__ == "__main__":
    from multiprocessing import freeze_support, Event
    freeze_support()
    print("🧪 Running one-shot altcoin conversion test (dev mode)...", flush=True)
    shared_event = Event()
    try:
        start_altcoin_conversion_process(shared_event, None, shared_event)
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("🛑 Ctrl+C received. Stopping...", flush=True)
        shared_event.set()
