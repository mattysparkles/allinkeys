"""
core/altcoin_derive.py

Converts VanitySearch .txt files to multi-coin .csv files.
Uses GPU (OpenCL) to derive addresses for: BTC, DOGE, LTC, DASH, BCH, RVN, PEP, ETH.
Preserves all columns and GPU pipeline. Flushes rows as it writes.
"""

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
_gpu_logged_once = False

from config.settings import (
    ENABLE_ALTCOIN_DERIVATION,
    ENABLE_SEED_VERIFICATION,
    DOGE, DASH, LTC, BCH, RVN, PEP, ETH,
    CSV_DIR, VANITY_OUTPUT_DIR
)
from core.logger import log_message
from core.dashboard import update_dashboard_stat
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


def derive_addresses_gpu(hex_keys):
    try:
        context, device = get_gpu_context_for_altcoin()
    except Exception as e:
        log_message(f"❌ GPU derivation failed: {e}", "ERROR")
        return [{"error": str(e)} for _ in hex_keys]

    queue = cl.CommandQueue(context)

    kernel_path = os.path.join(os.path.dirname(__file__), "sha256_kernel.cl")
    if not os.path.isfile(kernel_path):
        raise FileNotFoundError(f"❌ Missing kernel file: {kernel_path}")

    with open(kernel_path, "r", encoding="utf-8", errors="strict") as kf:
        kernel_code = kf.read()

    # Sanitize for PyOpenCL cache write: only ASCII allowed
    kernel_code = ''.join(c if 32 <= ord(c) <= 126 or c in '\n\r\t' else '?' for c in kernel_code)

    program = cl.Program(context, kernel_code).build()

    key_bytes = []
    for i, k in enumerate(hex_keys):
        try:
            cleaned = k[2:] if k.lower().startswith("0x") else k
            cleaned = cleaned.zfill(64)
            key_bytes.append(bytes.fromhex(cleaned))
        except Exception as e:
            log_message(f"⚠️ Invalid hex at index {i}: {k} — {e}", "WARNING")
            key_bytes.append(b'\x00' * 32)

    all_keys_flat = b''.join(key_bytes)
    count = len(key_bytes)

    mf = cl.mem_flags
    private_keys_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=all_keys_flat)
    output_buf = cl.Buffer(context, mf.WRITE_ONLY, 32 * count)
    program.derive_addresses(queue, (count,), None, private_keys_buf, output_buf, np.int32(count))

    derived_data = np.empty((count, 32), dtype=np.uint8)
    cl.enqueue_copy(queue, derived_data, output_buf)

    results = []
    for raw in derived_data:
        raw_bytes = bytes(raw)
        seed = int.from_bytes(raw_bytes, 'big') % (2**256)

        if seed == 0:
            results.append({'error': 'Invalid seed'})
            continue

        try:
            priv = seed.to_bytes(32, 'big')
            comp_pub = get_compressed_pubkey(priv)
            pubhash = hash160(comp_pub)

            result = {
                "btc_U": b58(b'\x00', pubhash),
                "btc_C": b58(b'\x00', pubhash),
                "ltc_U": b58(b'\x30', pubhash),
                "ltc_C": b58(b'\x30', pubhash),
                "doge_U": b58(b'\x1e', pubhash),
                "doge_C": b58(b'\x1e', pubhash),
                "dash_U": b58(b'\x4c', pubhash),
                "dash_C": b58(b'\x4c', pubhash),
                "bch_U": b58(b'\x00', pubhash),
                "bch_C": b58(b'\x00', pubhash),
                "rvn_U": b58(b'\x3c', pubhash),
                "rvn_C": b58(b'\x3c', pubhash),
                "pep_U": b58(b'\x37', pubhash),
                "pep_C": b58(b'\x37', pubhash),
                "eth": "0x" + keccak(comp_pub[1:])[-20:].hex()
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
    output_csv_path = os.path.join(CSV_DIR, f"{base_name}.csv")

    try:
        with open(input_txt_path, "rb") as infile_raw, \
             open(output_csv_path, "w", newline='', encoding="utf-8") as outfile:

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
            writer = csv.writer(outfile)

            headers = ["priv_hex", "btc_U", "btc_C"]
            if DOGE: headers += ["doge_U", "doge_C"]
            if DASH: headers += ["dash_U", "dash_C"]
            if LTC:  headers += ["ltc_U", "ltc_C"]
            if BCH:  headers += ["bch_U", "bch_C"]
            if RVN:  headers += ["rvn_U", "rvn_C"]
            if PEP:  headers += ["pep_U", "pep_C"]
            if ETH:  headers += ["eth"]
            writer.writerow(headers)
            outfile.flush()

            line_buffer = []
            rows_written = 0
            line_number = 0

            for line in infile:
                line_number += 1
                stripped = line.strip()
                if stripped.startswith("PubAddress:") or stripped.startswith("Pub Addr:"):
                    line_buffer = [stripped]
                elif stripped.startswith("Priv (WIF):") and line_buffer:
                    line_buffer.append(stripped)
                elif stripped.startswith("Priv (HEX):") and len(line_buffer) == 2:
                    line_buffer.append(stripped)
                    try:
                        pub = line_buffer[0].split(":", 1)[1].strip()
                        priv_hex = line_buffer[2].split(":", 1)[1].strip()
                        sanitized_hex = priv_hex.lower().replace("0x", "").zfill(64)
                        try:
                            raw_bytes = bytes.fromhex(sanitized_hex)
                            if len(raw_bytes) != 32:
                                raise ValueError(f"Invalid hex length: {len(raw_bytes)} bytes")
                        except Exception as hex_err:
                            log_message(f"⚠️ Skipping invalid priv_hex at line {line_number - 2}: {priv_hex} — {safe_str(hex_err)}", "WARNING")
                            continue

                        derived = derive_altcoin_addresses_from_hex(priv_hex)

                        btc_u = derived.get("btc_U", "")
                        btc_c = derived.get("btc_C", "")
                        if ENABLE_SEED_VERIFICATION and pub and btc_u != pub:
                            log_message(f"⚠️ BTC verification failed: {pub} vs {btc_u}")
                            continue

                        row = [priv_hex, btc_u, btc_c]
                        if DOGE: row += [derived.get("doge_U", ""), derived.get("doge_C", "")]
                        if DASH: row += [derived.get("dash_U", ""), derived.get("dash_C", "")]
                        if LTC:  row += [derived.get("ltc_U", ""), derived.get("ltc_C", "")]
                        if BCH:  row += [derived.get("bch_U", ""), derived.get("bch_C", "")]
                        if RVN:  row += [derived.get("rvn_U", ""), derived.get("rvn_C", "")]
                        if PEP:  row += [derived.get("pep_U", ""), derived.get("pep_C", "")]
                        if ETH:  row += [derived.get("eth", "")]
                        writer.writerow(row)
                        outfile.flush()
                        rows_written += 1
                    except Exception as e:
                        log_message(f"❌ Derivation failed at block starting line {line_number - 2}: {safe_str(e)}", "ERROR")
                    line_buffer = []
                else:
                    line_buffer = []

            if rows_written == 0:
                log_message(f"⚠️ No rows written for {input_txt_path}. Check parsing rules or input format.")
            log_message(f"✅ Created CSV: {output_csv_path} with {rows_written} rows", "INFO")
            update_dashboard_stat("csv_created", 1)
            if batch_id is not None:
                checkpoint.save_csv_checkpoint(batch_id, output_csv_path)

    except Exception as e:
        log_message(f"❌ Error processing {input_txt_path}: {safe_str(e)}", "ERROR")
        traceback.print_exc()


def convert_txt_to_csv_loop():
    log_message("📦 Altcoin conversion loop started...", "INFO")
    processed = set()

    while True:
        try:
            all_txt = [
                f for f in os.listdir(VANITY_OUTPUT_DIR)
                if f.endswith(".txt") and f not in processed
            ]

            for txt_file in all_txt:
                full_path = os.path.join(VANITY_OUTPUT_DIR, txt_file)
                batch_id = None
                convert_txt_to_csv(full_path, batch_id)
                processed.add(txt_file)

        except Exception as e:
            log_message(f"❌ Error in altcoin conversion loop: {safe_str(e)}", "ERROR")

        time.sleep(5)


def start_backlog_conversion_loop():
    process = multiprocessing.Process(target=convert_txt_to_csv_loop)
    process.start()
    log_message("🚀 Backlog converter process started...", "INFO")
