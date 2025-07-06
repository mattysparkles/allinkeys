"""
csv_checker.py – 📁 CSV scanning against complete list of funded address updated daily
"""

import os
import sys
import csv
import time
import io
import json
from datetime import datetime

# Increase CSV field size limit to handle large entries (up to 10MB)
csv.field_size_limit(10 * 1024 * 1024)
from config.settings import (
    CSV_DIR, UNIQUE_DIR, FULL_DIR, DOWNLOADS_DIR,
    CHECKED_CSV_LOG, RECHECKED_CSV_LOG,
    ENABLE_ALERTS, ENABLE_PGP, PGP_PUBLIC_KEY_PATH
)
from utils.file_utils import find_latest_funded_file
from config.coin_definitions import coin_columns
from core.alerts import alert_match
from core.logger import log_message
from utils.pgp_utils import encrypt_with_pgp
from core.dashboard import update_dashboard_stat, increment_metric, init_shared_metrics, set_metric, get_metric
from utils.balance_checker import fetch_live_balance

MATCHED_CSV_DIR = os.path.join(CSV_DIR, "matched_csv")
os.makedirs(MATCHED_CSV_DIR, exist_ok=True)

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

    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        log_message(f"❌ {filename} is empty or missing. Skipping.", "ERROR")
        return []

    log_message(f"🔎 Checking {filename}...")

    try:
        with open(csv_file, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

            if not headers:
                log_message(f"❌ {filename} missing headers. Skipping file.", "ERROR")
                return []

            known = {c for cols in coin_columns.values() for c in cols}
            safe_metadata_columns = {
                'original_seed', 'hex_key', 'private_key',
                'compressed_address', 'uncompressed_address',
                'batch_id', 'index'
            }
            unknown = [
                h for h in headers
                if h not in known and h not in safe_metadata_columns
            ]
            if unknown:
                log_message(f"⚠️ Unknown columns in {filename}: {unknown}", "WARN")

            for coin, columns in coin_columns.items():
                missing = [col for col in columns if col not in headers]
                if missing:
                    log_message(f"⚠️ {coin.upper()} columns missing in {filename}: {missing}", "WARN")
                else:
                    log_message(f"🔎 {coin.upper()} columns scanned: {columns}", "DEBUG")

            try:
                from core.dashboard import get_pause_event
                for row in reader:
                    if get_metric("global_run_state") == "paused" or (get_pause_event() and get_pause_event().is_set()):
                        time.sleep(1)
                        continue
                    rows_scanned += 1
                    try:
                        for coin, columns in coin_columns.items():
                            for col in columns:
                                raw = row.get(col)
                                addr = raw.strip() if raw else ""
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
                                        try:
                                            encrypted = encrypt_with_pgp(json.dumps(match_payload), PGP_PUBLIC_KEY_PATH)
                                            alert_match(match_payload)
                                            alert_match({"encrypted": encrypted})
                                        except Exception as pgp_err:
                                            log_message(f"⚠️ PGP failed for {addr}: {pgp_err}", "WARN")
                                            alert_match(match_payload)
                                    else:
                                        alert_match(match_payload)

                                    if filename != "test_alerts.csv":
                                        bal = fetch_live_balance(addr, coin)
                                        if bal is not None:
                                            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                                            log_message(
                                                f"🎯 Matched {coin.upper()} address {addr} – Current balance: {bal} {coin.upper()} (fetched at {ts})",
                                                "ALERT",
                                            )
                                        else:
                                            log_message(f"⚠️ Could not fetch balance for {addr}", "WARN")

                                    new_matches.add(addr)
                                    increment_metric("matched_keys", 1)
                                    increment_metric(f"matches_found_today.{coin}", 1)
                                    increment_metric(f"matches_found_lifetime.{coin}", 1)
                    except Exception as row_err:
                        log_message(f"❌ Skipping malformed row {rows_scanned} in {filename}: {row_err}", "ERROR")
                        continue
            except csv.Error as e:
                log_message(f"❌ CSV parsing error in {filename}: {e}", "ERROR")

        end_time = time.perf_counter()
        duration_sec = round(end_time - start_time, 2)
        CHECK_TIME_HISTORY.append(duration_sec)
        if len(CHECK_TIME_HISTORY) > MAX_HISTORY_SIZE:
            CHECK_TIME_HISTORY.pop(0)

        avg_time = round(sum(CHECK_TIME_HISTORY) / len(CHECK_TIME_HISTORY), 2)

        increment_metric("csv_checked_today", 1)
        increment_metric("csv_checked_lifetime", 1)
        if recheck:
            increment_metric("csv_rechecked_today", 1)
        update_dashboard_stat({
            "avg_check_time": avg_time,
            "last_check_duration": f"{duration_sec:.2f}s"
        })
        for coin in coin_columns:
            increment_metric(f"addresses_checked_today.{coin}", rows_scanned)
            increment_metric(f"addresses_checked_lifetime.{coin}", rows_scanned)

        log_message(f"✅ {'Recheck' if recheck else 'Check'} complete: {len(new_matches)} matches found", "INFO")
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
    try:
        init_shared_metrics(shared_metrics)
        set_metric("status.csv_check", True)
        set_metric("csv_checked_today", 0)
        set_metric("addresses_checked_today", {c: 0 for c in coin_columns})
        set_metric("matches_found_today", {c: 0 for c in coin_columns})
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", True)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)

    address_sets = {}
    for coin, columns in coin_columns.items():
        full_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR)
        if full_path:
            log_message(f"🔎 Using funded list {os.path.basename(full_path)} for {coin.upper()}.")
            address_sets[coin] = load_funded_addresses(full_path)
        else:
            log_message(f"⚠️ No funded list found for {coin.upper()} in DOWNLOADS_DIR", "WARN")

    combined_set = set.union(*address_sets.values()) if address_sets else set()

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if get_metric("global_run_state") == "paused" or (get_pause_event() and get_pause_event().is_set()):
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, CHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        check_csv_against_addresses(csv_path, combined_set)
        mark_csv_as_checked(filename, CHECKED_CSV_LOG)

    update_csv_eta()
    set_metric("status.csv_check", False)
    try:
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", False)
    except Exception:
        pass


def check_csvs(shared_metrics=None):
    try:
        init_shared_metrics(shared_metrics)
        set_metric("status.csv_recheck", True)
        set_metric("csv_rechecked_today", 0)
        set_metric("addresses_checked_today", {c: 0 for c in coin_columns})
        set_metric("matches_found_today", {c: 0 for c in coin_columns})
        from core.dashboard import set_thread_health
        set_thread_health("csv_recheck", True)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)

    address_sets = {}
    for coin, columns in coin_columns.items():
        unique_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR)
        if unique_path:
            log_message(f"🔎 Using unique list {os.path.basename(unique_path)} for {coin.upper()}.")
            address_sets[coin] = load_funded_addresses(unique_path)
        else:
            log_message(f"⚠️ No unique list found for {coin.upper()} in DOWNLOADS_DIR", "WARN")

    combined_set = set.union(*address_sets.values()) if address_sets else set()

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if get_metric("global_run_state") == "paused" or (get_pause_event() and get_pause_event().is_set()):
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, RECHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        check_csv_against_addresses(csv_path, combined_set, recheck=True)
        mark_csv_as_checked(filename, RECHECKED_CSV_LOG)

    update_csv_eta()
    set_metric("status.csv_recheck", False)
    try:
        from core.dashboard import set_thread_health
        set_thread_health("csv_recheck", False)
    except Exception:
        pass

def inject_test_match(test_address="1KFHE7w8BhaENAswwryaoccDb6qcT6DbYY"):
    test_csv = os.path.join(CSV_DIR, "test_match.csv")
    with open(test_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["btc_U", "wif"])
        writer.writeheader()
        writer.writerow({"btc_U": test_address, "wif": "TESTWIF"})

    matches = check_csv_against_addresses(test_csv, {test_address})
    os.remove(test_csv)
    return bool(matches)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check generated CSVs for funded addresses")
    parser.add_argument("--recheck", action="store_true", help="Run unique recheck mode")
    args = parser.parse_args()

    if args.recheck:
        check_csvs()
    else:
        check_csvs_day_one()
