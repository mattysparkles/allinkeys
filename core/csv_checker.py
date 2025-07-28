"""
csv_checker.py – 📁 CSV scanning against complete list of funded address updated daily
"""

import os
import csv
import time
import json
from datetime import datetime

from config.settings import (
    CSV_DIR,
    DOWNLOADS_DIR,
    CHECKED_CSV_LOG,
    RECHECKED_CSV_LOG,
    CSV_CHECKPOINT_STATE,
    ENABLE_PGP,
    PGP_PUBLIC_KEY_PATH,
    LOG_LEVEL,
)
from utils.file_utils import find_latest_funded_file
from core.alerts import alert_match
from config.coin_definitions import coin_columns
from core.logger import log_message
from utils.pgp_utils import encrypt_with_pgp
from core.dashboard import update_dashboard_stat, increment_metric, init_shared_metrics, set_metric, get_metric
from utils.balance_checker import fetch_live_balance
csv.field_size_limit(2**30)  # 1GB

MATCHED_CSV_DIR = os.path.join(CSV_DIR, "matched_csv")
os.makedirs(MATCHED_CSV_DIR, exist_ok=True)

CHECK_TIME_HISTORY = []
MAX_HISTORY_SIZE = 10

def load_csv_state():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not os.path.exists(CSV_CHECKPOINT_STATE):
        return {"date": today, "files": {}}
    try:
        with open(CSV_CHECKPOINT_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today:
            return {"date": today, "files": {}}
        if "files" not in data:
            data["files"] = {}
        return data
    except Exception:
        return {"date": today, "files": {}}


def save_csv_state(state):
    try:
        with open(CSV_CHECKPOINT_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log_message(f"⚠️ Failed to save CSV state: {e}", "WARN")

def normalize_address(addr: str) -> str:
    """Return a normalized version of ``addr`` for matching.

    Currently this strips the ``bitcoincash:`` prefix used by some BCH
    addresses and trims surrounding whitespace. Additional normalization
    rules can be added here as needed.
    """
    if not addr:
        return ""
    addr = addr.strip()
    if addr.lower().startswith("bitcoincash:"):
        addr = addr.split(":", 1)[1]
    return addr

def update_csv_eta():
    try:
        all_files = [
            f for f in os.listdir(CSV_DIR)
            if f.endswith(".csv") and not f.endswith(".partial.csv")
        ]
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

def scan_csv_for_oversized_lines(csv_path, threshold=10_000_000):
    """Scan a CSV file and report any lines exceeding the threshold size."""
    try:
        with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if len(line) > threshold:
                    print(
                        f"\U0001F6A8 Line {i+1} in {csv_path} exceeds {threshold} bytes"
                    )
    except Exception as e:
        log_message(f"⚠️ Failed scanning {csv_path}: {e}", "WARNING")

def load_funded_addresses(file_path):
    """Load funded addresses from ``file_path`` applying ``normalize_address``
    to each line so comparisons are consistent."""
    with open(file_path, "r") as f:
        return set(normalize_address(line.strip()) for line in f.readlines())

def check_csv_against_addresses(csv_file, address_sets, recheck=False, safe_mode=False, pause_event=None, shutdown_event=None, start_row=0, state=None):
    new_matches = set()
    all_matches = []
    filename = os.path.basename(csv_file)
    rows_scanned = 0
    start_time = time.perf_counter()
    set_metric("csv_checker.last_file", filename)
    set_metric("last_csv_checked_filename", filename)
    set_metric("csv_checker.last_timestamp", datetime.utcnow().isoformat())
    set_metric("csv_checker.rows_checked", 0)
    set_metric("csv_checker.matches_found", 0)

    if filename.endswith(".partial.csv"):
        return []
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        log_message(f"❌ {filename} is empty or missing. Skipping.", "ERROR")
        return []

    if start_row:
        log_message(f"🔎 Resuming {filename} from row {start_row}...")
    else:
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
                for row_num, row in enumerate(reader, start=1):
                    if shutdown_event and shutdown_event.is_set():
                        break
                    if row_num <= start_row:
                        continue
                    if pause_event and pause_event.is_set():
                        while pause_event and pause_event.is_set():
                            time.sleep(0.2)
                        if pause_event.is_set():
                            continue
                    rows_scanned = row_num
                    increment_metric("csv_checker.rows_checked", 1)
                    set_metric("csv_checker.rows_checked", rows_scanned)
                    if state and row_num % 1000 == 0:
                        state.setdefault("files", {})[filename] = row_num
                        save_csv_state(state)
                    if rows_scanned % 10000 == 0:
                        log_message(f"[Progress] {filename}: {rows_scanned} rows scanned", "DEBUG")
                    row_matches = []
                    try:
                        for coin, columns in coin_columns.items():
                            # Iterate through every address column so multiple
                            # matches within a single row are all detected.
                            for col in columns:
                                raw = row.get(col)
                                addr = raw.strip() if raw else ""
                                normalized = normalize_address(addr)
                                if normalized != addr and LOG_LEVEL == "DEBUG":
                                    log_message(
                                        f"[Checker] Normalized BCH address: {addr} → {normalized}",
                                        "DEBUG",
                                    )
                                in_funded = normalized in address_sets.get(coin, set())
                                log_message(
                                    f"[Checker] {coin.upper()} column '{col}' -> '{normalized}' : {'MATCH' if in_funded else 'miss'}",
                                    "DEBUG",
                                )
                                if addr and in_funded:
                                    try:
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

                                        if normalized not in new_matches:
                                            new_matches.add(normalized)
                                            increment_metric("matched_keys", 1)
                                            increment_metric(f"matches_found_today.{coin}", 1)
                                            if filename != "test_alerts.csv":
                                                increment_metric(f"matches_found_lifetime.{coin}", 1)
                                            update_dashboard_stat("matches_found_today", get_metric("matches_found_today"))
                                            update_dashboard_stat("matches_found_lifetime", get_metric("matches_found_lifetime"))
                                        row_matches.append(addr)
                                        all_matches.append(match_payload)
                                        # Continue scanning the row for additional matches
                                    except Exception as match_err:
                                        log_message(f"⚠️ Match processing error for {addr}: {match_err}", "WARN")
                                        continue
                    except Exception as row_err:
                        if safe_mode:
                            log_message(
                                f"⚠️ Row skipped due to error in {csv_file}: {row_err}",
                                "WARNING",
                            )
                            continue
                        else:
                            raise
                    if row_matches:
                        increment_metric("csv_checker.matches_found", len(row_matches))
                        set_metric("csv_checker.matches_found", get_metric("csv_checker.matches_found"))
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
        update_dashboard_stat("csv_checked_today", get_metric("csv_checked_today"))
        update_dashboard_stat("csv_checked_lifetime", get_metric("csv_checked_lifetime"))
        if recheck:
            increment_metric("csv_rechecked_today", 1)
            increment_metric("csv_rechecked_lifetime", 1)
        update_dashboard_stat({
            "avg_check_time": avg_time,
            "last_check_duration": f"{duration_sec:.2f}s"
        })
        for coin in coin_columns:
            increment_metric(f"addresses_checked_today.{coin}", rows_scanned)
            increment_metric(f"addresses_checked_lifetime.{coin}", rows_scanned)
        update_dashboard_stat("addresses_checked_today", get_metric("addresses_checked_today"))
        update_dashboard_stat("addresses_checked_lifetime", get_metric("addresses_checked_lifetime"))

        log_message(f"✅ {'Recheck' if recheck else 'Check'} complete: {len(new_matches)} matches found", "INFO")
        log_message(f"📄 {filename}: {rows_scanned:,} rows scanned | {len(new_matches)} unique matches | ⏱️ Time: {duration_sec:.2f}s", "INFO")

        if state and filename in state.get("files", {}):
            state["files"].pop(filename, None)
            save_csv_state(state)

        if new_matches:
            dest_path = os.path.join(MATCHED_CSV_DIR, filename)
            try:
                os.rename(csv_file, dest_path)
                log_message(f"📂 Moved matched CSV to {dest_path}", "INFO")
            except Exception as e:
                log_message(f"⚠️ Failed to move {filename} to matched_csv/: {e}", "WARNING")

        return all_matches

    except Exception as e:
        log_message(f"❌ Error reading {filename}: {str(e)}", "ERROR")
        return []

from core.logger import initialize_logging

def check_csvs_day_one(shared_metrics=None, shutdown_event=None, pause_event=None, safe_mode=False, log_q=None):
    initialize_logging(log_q)
    try:
        init_shared_metrics(shared_metrics)
        from core.dashboard import register_control_events
        register_control_events(shutdown_event, pause_event, module="csv_check")
        set_metric("status.csv_check", "Running")
        set_metric("csv_checked_today", 0)
        set_metric("addresses_checked_today", {c: 0 for c in coin_columns})
        set_metric("matches_found_today", {c: 0 for c in coin_columns})
        set_metric("csv_checker", {"rows_checked": 0, "matches_found": 0, "last_file": ""})
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", True)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
        log_message("🟢 CSV day-one checker started", "DEBUG")
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)

    address_sets = {}
    state = load_csv_state()
    state = load_csv_state()
    for coin, columns in coin_columns.items():
        full_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR, unique=False)
        if full_path:
            log_message(f"🔎 Using funded list {os.path.basename(full_path)} for {coin.upper()}.")
            address_sets[coin] = load_funded_addresses(full_path)
        else:
            log_message(f"⚠️ No funded list found for {coin.upper()} in DOWNLOADS_DIR", "WARN")

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if shutdown_event and shutdown_event.is_set():
            break
        if filename.endswith(".partial.csv"):
            final = filename.replace(".partial.csv", ".csv")
            if os.path.exists(os.path.join(CSV_DIR, final)):
                log_message(f"ℹ️ Skipping {filename} because final CSV already exists", "INFO")
            continue
        if get_pause_event("csv_check") and get_pause_event("csv_check").is_set():
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, CHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        start_row = state.get("files", {}).get(filename, 0)
        check_csv_against_addresses(
            csv_path,
            address_sets,
            safe_mode=safe_mode,
            pause_event=pause_event,
            shutdown_event=shutdown_event,
            start_row=start_row,
            state=state,
        )
        mark_csv_as_checked(filename, CHECKED_CSV_LOG)

    update_csv_eta()
    set_metric("status.csv_check", "Stopped")
    try:
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", False)
    except Exception:
        pass


def check_csvs(shared_metrics=None, shutdown_event=None, pause_event=None, safe_mode=False, log_q=None):
    initialize_logging(log_q)
    try:
        init_shared_metrics(shared_metrics)
        from core.dashboard import register_control_events
        register_control_events(shutdown_event, pause_event, module="csv_recheck")
        set_metric("status.csv_recheck", "Running")
        set_metric("csv_rechecked_today", 0)
        set_metric("addresses_checked_today", {c: 0 for c in coin_columns})
        set_metric("matches_found_today", {c: 0 for c in coin_columns})
        set_metric("csv_checker", {"rows_checked": 0, "matches_found": 0, "last_file": ""})
        from core.dashboard import set_thread_health
        set_thread_health("csv_recheck", True)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
        log_message("🟢 CSV recheck checker started", "DEBUG")
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)

    address_sets = {}
    state = load_csv_state()
    for coin, columns in coin_columns.items():
        unique_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR, unique=True)
        if unique_path:
            log_message(f"🔎 Using unique list {os.path.basename(unique_path)} for {coin.upper()}.")
            address_sets[coin] = load_funded_addresses(unique_path)
        else:
            log_message(f"⚠️ No unique list found for {coin.upper()} in DOWNLOADS_DIR", "WARN")

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if shutdown_event and shutdown_event.is_set():
            break
        if filename.endswith(".partial.csv"):
            final = filename.replace(".partial.csv", ".csv")
            if os.path.exists(os.path.join(CSV_DIR, final)):
                log_message(f"ℹ️ Skipping {filename} because final CSV already exists", "INFO")
            continue
        if get_pause_event("csv_recheck") and get_pause_event("csv_recheck").is_set():
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, RECHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        start_row = state.get("files", {}).get(filename, 0)
        check_csv_against_addresses(
            csv_path,
            address_sets,
            recheck=True,
            safe_mode=safe_mode,
            pause_event=pause_event,
            shutdown_event=shutdown_event,
            start_row=start_row,
            state=state,
        )
        mark_csv_as_checked(filename, RECHECKED_CSV_LOG)

    update_csv_eta()
    set_metric("status.csv_recheck", "Stopped")
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

    matches = check_csv_against_addresses(test_csv, {"btc": {test_address}}, safe_mode=True)
    os.remove(test_csv)
    return bool(matches)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check generated CSVs for funded addresses")
    parser.add_argument("--recheck", action="store_true", help="Run unique recheck mode")
    parser.add_argument("--safe", action="store_true", help="Enable safe CSV parsing")
    parser.add_argument("--scan", metavar="CSV", help="Scan specified CSV for oversized lines")
    parser.add_argument("--threshold", type=int, default=10_000_000, help="Byte threshold for --scan")
    args = parser.parse_args()

    from core.logger import start_listener, log_queue
    start_listener()
    if args.scan:
        scan_csv_for_oversized_lines(args.scan, threshold=args.threshold)
    elif args.recheck:
        check_csvs(safe_mode=args.safe, log_q=log_queue)
    else:
        check_csvs_day_one(safe_mode=args.safe, log_q=log_queue)
