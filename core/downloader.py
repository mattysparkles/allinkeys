# core/downloader.py

import os
import gzip
import shutil
import requests
from datetime import datetime
from glob import glob

from config.settings import COIN_DOWNLOAD_URLS, DOWNLOAD_DIR, MAX_DAILY_FILES_PER_COIN
from core.logger import log_message

def download_and_compare_address_lists():
    """
    Downloads and processes funded address lists for each coin.
    Creates full and unique lists, keeps only the 2 latest files.
    """
    for coin, url in COIN_DOWNLOAD_URLS.items():
        try:
            now = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            output_full = os.path.join(DOWNLOAD_DIR, f"{coin.upper()}_addresses_{now}.txt")
            output_unique = os.path.join(DOWNLOAD_DIR, f"{coin.upper()}_UNIQUE_addresses_{now}.txt")
            gz_path = output_full + ".gz"

            # Download file
            r = requests.get(url, stream=True, timeout=30)
            r.raise_for_status()
            with open(gz_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            log_message(f"{coin.upper()}: Download complete")

            # Decompress .gz file
            with gzip.open(gz_path, 'rb') as f_in, open(output_full, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(gz_path)
            log_message(f"{coin.upper()}: Decompressed to {output_full}")

            # Special BTC handling: filter only addresses starting with '1'
            if coin == "btc":
                with open(output_full, 'r', encoding='utf-8') as f:
                    filtered = [line for line in f if line.startswith("1")]
                with open(output_full, 'w', encoding='utf-8') as f:
                    f.writelines(filtered)
                log_message(f"{coin.upper()}: Filtered for addresses starting with '1'")

            # Compare with previous file
            previous_files = sorted(glob(os.path.join(DOWNLOAD_DIR, f"{coin.upper()}_addresses_*.txt")))
            if len(previous_files) >= 2:
                previous_file = previous_files[-2]
                with open(previous_file, 'r', encoding='utf-8') as f:
                    old_addrs = set(line.strip() for line in f if line.strip())

                with open(output_full, 'r', encoding='utf-8') as f:
                    new_addrs = set(line.strip() for line in f if line.strip())

                newly_funded = sorted(new_addrs - old_addrs)
                with open(output_unique, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(newly_funded))
                log_message(f"{coin.upper()}: {len(newly_funded)} new addresses written to {output_unique}")

            # Prune excess files
            for pattern in [f"{coin.upper()}_addresses_*.txt", f"{coin.upper()}_UNIQUE_addresses_*.txt"]:
                files = sorted(glob(os.path.join(DOWNLOAD_DIR, pattern)))
                while len(files) > MAX_DAILY_FILES_PER_COIN:
                    to_delete = files.pop(0)
                    os.remove(to_delete)
                    log_message(f"{coin.upper()}: Deleted old file {to_delete}")

        except Exception as e:
            log_message(f"❌ {coin.upper()} download failed: {str(e)}", "ERROR")
