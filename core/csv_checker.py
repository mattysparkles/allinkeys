"""
csv_checker.py – 📁 CSV scanning against complete list of funded address updated daily
"""

import os
import csv
import time
from datetime import datetime
from config.settings import (
    CSV_DIR, UNIQUE_DIR, FULL_DIR, CHECKED_CSV_LOG, RECHECKED_CSV_LOG,
    ENABLE_ALERTS, ENABLE_PGP, PGP_PUBLIC_KEY_PATH
)
from config.coin_definitions import coin_columns
from core.alerts import alert_match
from core.logger import log_message
from utils.pgp_utils import encrypt_with_pgp
from core.dashboard import update_dashboard_stat, increment_metric, init_shared_metrics

MATCHED_CSV_DIR = os.path.join(CSV_DIR, "matched_csv")
os.makedirs(MATCHED_CSV_DIR, exist_ok=True)
# Track last 10 check times for rolling average
CHECK_TIME_HISTORY = []
MAX_HISTORY_SIZE = 10

def update_csv_eta():
    try:
        all_files = [f for f in os.listdir(CSV_DIR) if f.endswith(".csv")]
        remaining_files = [
            f for f in all_files
            if not has_been_checked(f, CHECKED_CSV_LOG) and not has_been_checked(f, RECHECKED_CSV_LOG)
        ]
        files_left = len(remaining_files)
        if CHECK_TIME_HISTORY:
            avg_time = sum(CHECK_TIME_HISTORY) / len(CHECK_TIME_HISTORY)
            eta_seconds = round(files_left * avg_time)
            hours = eta_seconds // 3600
            minutes = (eta_seconds % 3600) // 60
            seconds = eta_seconds % 60
            eta_str = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            eta_str = "N/A"
        update_dashboard_stat("csv_eta", eta_str)
    except Exception as e:
        log_message(f"⚠️ Failed to update CSV ETA: {e}", "WARN")

def mark_csv_as_checked(filename, log_file):
    with open(log_file, "a") as f:
        f.write(f"{filename}\n")

def has_been_checked(filename, log_file):
    if not os.path.exists(log_file):
        return False
    with open(log_file, "r") as f:
        return filename in f.read()

def load_funded_addresses(file_path):
    with open(file_path, "r") as f:
        return set(line.strip() for line in f.readlines())

def check_csv_against_addresses(csv_file, address_set, recheck=False):
    new_matches = set()
    filename = os.path.basename(csv_file)
    check_type = "Recheck" if recheck else "First Check"
    rows_scanned = 0
    start_time = time.perf_counter()

    log_message(f"🔍 {check_type} STARTED: {filename}")

    try:
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

            if not headers:
                log_message(f"❌ {filename} missing headers. Skipping file.", "ERROR")
                return []

            # Validate expected headers
            for coin, columns in coin_columns.items():
                missing = [col for col in columns if col not in headers]
                if missing:
                    log_message(f"⚠️ {coin.upper()} columns missing in {filename}: {missing}", "WARN")
                else:
                    log_message(f"🔎 {coin.upper()} columns scanned: {columns}", "DEBUG")

            for row in reader:
                rows_scanned += 1
                for coin, columns in coin_columns.items():
                    for col in columns:
                        addr = row.get(col, "").strip()
                        if addr and addr in address_set and addr not in new_matches:
                            match_payload = {
                                "timestamp": datetime.utcnow().isoformat(),
                                "coin": coin,
                                "address": addr,
                                "csv_file": filename,
                                "privkey": row.get("wif") or row.get("private_key") or row.get("priv_hex") or "unknown",
                                "batch_id": row.get("batch_id", "n/a"),
                                "index": row.get("index", "n/a"),
                                "row_number": rows_scanned
                            }

                            log_message(f"✅ MATCH FOUND: {addr} ({coin}) | File: {filename} | Row: {rows_scanned}", "ALERT")

                            if ENABLE_PGP:
                                encrypted = encrypt_with_pgp(json.dumps(match_payload), PGP_PUBLIC_KEY_PATH)
                                alert_match({"encrypted": encrypted})
                            else:
                                alert_match(match_payload)

                            new_matches.add(addr)
                            increment_metric("matched_keys", 1)
                            increment_metric(f"matches_found_today.{coin}", 1)

        end_time = time.perf_counter()
        duration_sec = round(end_time - start_time, 2)

        # Rolling average logic
        CHECK_TIME_HISTORY.append(duration_sec)
        if len(CHECK_TIME_HISTORY) > MAX_HISTORY_SIZE:
            CHECK_TIME_HISTORY.pop(0)

        avg_time = round(sum(CHECK_TIME_HISTORY) / len(CHECK_TIME_HISTORY), 2)

        increment_metric("csv_checked_today", 1)
        increment_metric("csv_checked_lifetime", 1)
        update_dashboard_stat({
            "avg_check_time": avg_time,
            "last_check_duration": f"{duration_sec:.2f}s"
        })
        for coin in coin_columns:
            increment_metric(f"addresses_checked_today.{coin}", rows_scanned)
            increment_metric(f"addresses_checked_lifetime.{coin}", rows_scanned)

        log_message(f"📄 {filename}: {rows_scanned:,} rows scanned | {len(new_matches)} unique matches | ⏱️ Time: {duration_sec:.2f}s", "INFO")

        if new_matches:
            dest_path = os.path.join(MATCHED_CSV_DIR, filename)
            try:
                os.rename(csv_file, dest_path)
                log_message(f"📂 Moved matched CSV to {dest_path}", "INFO")
            except Exception as e:
                log_message(f"⚠️ Failed to move {filename} to matched_csv/: {e}", "WARNING")

        return list(new_matches)

    except Exception as e:
        log_message(f"❌ Error reading {filename}: {str(e)}", "ERROR")
        return []




def check_csvs_day_one(shared_metrics=None):
    init_shared_metrics(shared_metrics)
    address_sets = {}
    for coin, columns in coin_columns.items():
        full_path = os.path.join(FULL_DIR, f"{coin}_funded.txt")
        if os.path.exists(full_path):
            address_sets[coin] = load_funded_addresses(full_path)

    combined_set = set.union(*address_sets.values()) if address_sets else set()

    for filename in os.listdir(CSV_DIR):
        if not filename.endswith(".csv") or has_been_checked(filename, CHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        check_csv_against_addresses(csv_path, combined_set)
        mark_csv_as_checked(filename, CHECKED_CSV_LOG)
   
    update_csv_eta()

def check_csvs(shared_metrics=None):
    init_shared_metrics(shared_metrics)
    address_sets = {}
    for coin, columns in coin_columns.items():
        unique_path = os.path.join(UNIQUE_DIR, f"{coin}_UNIQUE.txt")
        if os.path.exists(unique_path):
            address_sets[coin] = load_funded_addresses(unique_path)

    combined_set = set.union(*address_sets.values()) if address_sets else set()

    for filename in os.listdir(CSV_DIR):
        if not filename.endswith(".csv") or has_been_checked(filename, RECHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        check_csv_against_addresses(csv_path, combined_set, recheck=True)
        mark_csv_as_checked(filename, RECHECKED_CSV_LOG)
    
    update_csv_eta()


def inject_test_match(test_address="1KFHE7w8BhaENAswwryaoccDb6qcT6DbYY"):
    """Helper to append a known funded address and trigger a check."""
    test_csv = os.path.join(CSV_DIR, "test_match.csv")
    with open(test_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["btc_U", "wif"])
        writer.writeheader()
        writer.writerow({"btc_U": test_address, "wif": "TESTWIF"})

    matches = check_csv_against_addresses(test_csv, {test_address})
    os.remove(test_csv)
    return bool(matches)
