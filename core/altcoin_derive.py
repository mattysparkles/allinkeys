"""
core/altcoin_derive.py

Converts VanitySearch .txt files to multi-coin .csv files.
Uses GPU (OpenCL) to derive addresses for: BTC, DOGE, LTC, DASH, BCH, RVN, PEP, ETH.
Preserves all columns and GPU pipeline. Flushes rows as it writes.
"""

import os
import csv
import traceback
import hashlib
import base58
import pyopencl as cl
import numpy as np
from eth_hash.auto import keccak
from ecdsa import SigningKey, SECP256k1

from config.settings import (
    ENABLE_ALTCOIN_DERIVATION,
    ENABLE_SEED_VERIFICATION,
    DOGE, DASH, LTC, BCH, RVN, PEP, ETH,
    CSV_DIR
)
from core.logger import log_message
from core.dashboard import update_dashboard_stat
import core.checkpoint as checkpoint


def get_compressed_pubkey(priv_bytes):
    """Return the 33-byte compressed public key for the given private key."""
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


def get_amd_gpu_device():
    for platform in cl.get_platforms():
        for device in platform.get_devices():
            if "gfx1032" in device.name.lower() or "rx 6600" in device.name.lower():
                context = cl.Context([device])
                return context, device
    raise RuntimeError("❌ AMD RX 6600 GPU not found.")


def derive_addresses_gpu(hex_keys):
    context, device = get_amd_gpu_device()
    queue = cl.CommandQueue(context)

    kernel_path = os.path.join(os.path.dirname(__file__), "sha256_kernel.cl")
    if not os.path.isfile(kernel_path):
        raise FileNotFoundError(f"❌ Missing kernel file: {kernel_path}")

    with open(kernel_path, "r") as kf:
        kernel_code = kf.read()

    program = cl.Program(context, kernel_code).build()

    key_bytes = [bytes.fromhex(k.lstrip("0x").zfill(64)) for k in hex_keys]
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
                        line = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        # Fallback decoding: replace problematic characters and log
                        line = raw.decode("utf-8", errors="replace")
                        log_message(f"⚠️ Non-UTF8 character replaced in line {i}: {repr(line)}")
                    yield line

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
                if stripped.startswith("Pub Addr:"):
                    line_buffer = [stripped]
                elif stripped.startswith("Priv (WIF):") and line_buffer:
                    line_buffer.append(stripped)
                elif stripped.startswith("Priv (HEX):") and len(line_buffer) == 2:
                    line_buffer.append(stripped)
                    try:
                        pub = line_buffer[0].split(":", 1)[1].strip()
                        priv_hex = line_buffer[2].split(":", 1)[1].strip()
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
                        log_message(f"❌ Derivation failed at block starting line {line_number - 2}: {e}", "ERROR")
                    line_buffer = []
                else:
                    line_buffer = []

            log_message(f"✅ Created CSV: {output_csv_path} with {rows_written} rows", "INFO")
            update_dashboard_stat("csv_created", 1)
            if batch_id is not None:
                checkpoint.save_csv_checkpoint(batch_id, output_csv_path)

    except Exception as e:
        log_message(f"❌ Error processing {input_txt_path}: {e}", "ERROR")
        traceback.print_exc()
