# core/downloader.py

import os
import gzip
import shutil
import csv
import requests
from datetime import datetime
from glob import glob

from utils.file_utils import find_latest_funded_file

from config.settings import (
    COIN_DOWNLOAD_URLS,
    DOWNLOADS_DIR,
    MAX_DAILY_FILES_PER_COIN
)
from config.coin_definitions import coin_columns
from core.logger import log_message


def parse_address_lines(file_obj):
    """Yield cleaned addresses from open file object."""
    for idx, line in enumerate(file_obj):
        if idx == 0 and line.lower().startswith(("address", "address,balance")):
            continue
        addr = line.split(',', 1)[0].strip()
        if addr:
            yield addr


def clean_address_file(file_path):
    """Read, clean and overwrite an address file in place."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            addresses = list(parse_address_lines(f))
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(addresses))
    except Exception as exc:
        log_message(f"⚠️ Failed to clean {file_path}: {exc}", "WARN")


def generate_test_csv():
    """Create a test CSV using the first two addresses from funded lists."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    test_csv_path = os.path.join(DOWNLOADS_DIR, "test_alerts.csv")

    if os.path.exists(test_csv_path):
        return test_csv_path

    headers = [
        "original_seed", "hex_key",
        "btc_C", "btc_U", "ltc_C", "ltc_U",
        "doge_C", "doge_U", "bch_C", "bch_U",
        "eth",  # ✅ Single address format for ETH
        "dash_C", "dash_U", "rvn_C", "rvn_U",
        "pep_C", "pep_U",
        "private_key", "compressed_address", "uncompressed_address",
        "batch_id", "index"
    ]

    row = {h: "" for h in headers}
    row.update({
        "original_seed": "TESTSEED",
        "hex_key": "DEADBEEF",
        "private_key": "TESTPRIV",
        "compressed_address": "TESTCOMP",
        "uncompressed_address": "TESTUNCOMP",
        "batch_id": "0",
        "index": "0",
    })

    found_any = False
    for coin in coin_columns.keys():
        file_path = find_latest_funded_file(coin, DOWNLOADS_DIR)
        if not file_path:
            log_message(f"⚠️ No funded list found for {coin.upper()}.", "WARN")
            continue
        log_message(f"📥 Using funded list {os.path.basename(file_path)} for {coin.upper()}.")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()][:2]
                if lines:
                    if coin == "eth":
                        row["eth"] = lines[0]
                    else:
                        row[f"{coin}_C"] = lines[0]
                        row[f"{coin}_U"] = lines[1] if len(lines) > 1 else lines[0]
                    found_any = True
        except Exception as exc:
            log_message(f"⚠️ Failed reading {file_path}: {exc}", "WARN")

    if not found_any:
        log_message("⚠️ No funded address files found for test CSV", "WARN")
        return None

    try:
        with open(test_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerow(row)
        log_message(f"📝 Generated test CSV at {test_csv_path}")
    except Exception as e:
        log_message(f"❌ Failed to write test CSV: {e}", "ERROR")

    return test_csv_path

def download_and_compare_address_lists():
    """
    Downloads and processes funded address lists for each coin.
    Creates full and unique lists, keeps only the 2 latest files.
    """
    for coin, url in COIN_DOWNLOAD_URLS.items():
        try:
            now = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            today_prefix = datetime.utcnow().strftime("%Y-%m-%d")
            pattern_today = os.path.join(
                DOWNLOADS_DIR, f"{coin.upper()}_addresses_{today_prefix}*.txt"
            )
            if glob(pattern_today):
                log_message(f"{coin.upper()}: Already downloaded today, skipping")
                continue
            output_full = os.path.join(DOWNLOADS_DIR, f"{coin.upper()}_addresses_{now}.txt")
            output_unique = os.path.join(DOWNLOADS_DIR, f"{coin.upper()}_UNIQUE_addresses_{now}.txt")
            gz_path = output_full + ".gz"

            # Download file
            r = requests.get(url, stream=True, timeout=30)
            r.raise_for_status()
            with open(gz_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            log_message(f"{coin.upper()}: Download complete")

            # Check for GZIP magic header
            with open(gz_path, 'rb') as test_f:
                magic = test_f.read(2)
            is_gzipped = magic == b'\x1f\x8b'

            if is_gzipped:
                # Decompress .gz file
                with gzip.open(gz_path, 'rb') as f_in, open(output_full, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(gz_path)
                log_message(f"{coin.upper()}: Decompressed to {output_full}")
            else:
                # Treat as raw text file
                shutil.move(gz_path, output_full)
                log_message(f"{coin.upper()}: File was not gzipped. Saved as-is.")

            # BTC: Filter only addresses starting with '1'
            if coin == "btc":
                with open(output_full, 'r', encoding='utf-8') as f:
                    filtered = [line for line in f if line.startswith("1")]
                with open(output_full, 'w', encoding='utf-8') as f:
                    f.writelines(filtered)
                log_message(f"{coin.upper()}: Filtered for addresses starting with '1'")

            # Clean downloaded file to keep addresses only
            clean_address_file(output_full)

            # Compare with previous file
            previous_files = sorted(glob(os.path.join(DOWNLOADS_DIR, f"{coin.upper()}_addresses_*.txt")))
            if len(previous_files) >= 2:
                previous_file = previous_files[-2]
                with open(previous_file, 'r', encoding='utf-8') as f:
                    old_addrs = set(parse_address_lines(f))

                with open(output_full, 'r', encoding='utf-8') as f:
                    new_addrs = set(parse_address_lines(f))

                newly_funded = sorted(new_addrs - old_addrs)
                with open(output_unique, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(newly_funded))
                clean_address_file(output_unique)
                log_message(f"{coin.upper()}: {len(newly_funded)} new addresses written to {output_unique}")

            # Prune excess files
            for pattern in [f"{coin.upper()}_addresses_*.txt", f"{coin.upper()}_UNIQUE_addresses_*.txt"]:
                files = sorted(glob(os.path.join(DOWNLOADS_DIR, pattern)))
                while len(files) > MAX_DAILY_FILES_PER_COIN:
                    to_delete = files.pop(0)
                    os.remove(to_delete)
                    log_message(f"{coin.upper()}: Deleted old file {to_delete}")

        except Exception as e:
            log_message(f"❌ {coin.upper()} download failed: {str(e)}", "ERROR")

    # Generate test CSV even when downloads are skipped
    generate_test_csv()
