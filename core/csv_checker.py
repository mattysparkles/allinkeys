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
    NORMALIZE_BECH32_LOWER,
)
from utils.file_utils import find_latest_funded_file
from core.alerts import alert_match
from config.coin_definitions import coin_columns
from core.logger import get_logger
from utils.pgp_utils import encrypt_with_pgp
from core.dashboard import update_dashboard_stat, increment_metric, init_shared_metrics, set_metric, get_metric
from utils.balance_checker import fetch_live_balance
from core.downloader import load_btc_funded_multi
csv.field_size_limit(2**30)  # 1GB

MATCHED_CSV_DIR = os.path.join(CSV_DIR, "matched_csv")
os.makedirs(MATCHED_CSV_DIR, exist_ok=True)

# Dedicated logger for this module
logger = get_logger(__name__)

CHECK_TIME_HISTORY = []
MAX_HISTORY_SIZE = 10

def load_csv_state():
    """Load CSV checkpoint state to resume partially processed files."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not os.path.exists(CSV_CHECKPOINT_STATE):
        # Warn so users know a new scan will not resume from previous progress
        logger.warning(f"CSV checkpoint state missing at {CSV_CHECKPOINT_STATE}; starting fresh")
        return {"date": today, "files": {}}
    try:
        with open(CSV_CHECKPOINT_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded CSV state from {CSV_CHECKPOINT_STATE}")
        if data.get("date") != today:
            return {"date": today, "files": {}}
        if "files" not in data:
            data["files"] = {}
        return data
    except Exception:
        logger.exception("Failed to load CSV state")
        return {"date": today, "files": {}}


def save_csv_state(state):
    """Persist CSV checkpoint state to disk."""
    try:
        with open(CSV_CHECKPOINT_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        logger.info(f"Saved CSV state to {CSV_CHECKPOINT_STATE}")
    except Exception:
        logger.exception("Failed to save CSV state")

def detect_btc_address_type(addr: str) -> str:
    """
    Returns one of: 'p2pkh', 'p2sh', 'p2wpkh', 'taproot', 'p2wsh', or 'unknown'.
    Normalizes bech32 to lowercase if settings.NORMALIZE_BECH32_LOWER.
    """
    a = addr.strip()
    if not a:
        return "unknown"
    if a[0] == '1':
        return 'p2pkh'
    if a[0] == '3':
        return 'p2sh'
    al = a.lower()
    if al.startswith('bc1'):
        if NORMALIZE_BECH32_LOWER:
            a = al
        if al.startswith('bc1q'):
            return 'p2wpkh'
        if al.startswith('bc1p'):
            return 'taproot'
        return 'unknown'
    return 'unknown'


def normalize_address(addr: str) -> str:
    """Normalize address for comparison: bech32 -> lowercase; others unchanged."""
    if not addr:
        return addr
    al = addr.strip()
    if al.lower().startswith('bc1') and NORMALIZE_BECH32_LOWER:
        return al.lower()
    if al.lower().startswith("bitcoincash:"):
        return al.split(":", 1)[1]
    return al

def update_csv_eta():
    """Estimate remaining time for CSV scanning and push to dashboard."""
    try:
        all_files = [
            f
            for f in os.listdir(CSV_DIR)
            if f.endswith(".csv") and not f.endswith(".partial.csv")
        ]  # Do not process .partial.csv (in-progress) files; only finalized .csv
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
        logger.debug(f"CSV ETA updated to {eta_str} for {files_left} files")
    except Exception:
        logger.exception("Failed to update CSV ETA")

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
    except Exception:
        logger.exception(f"Failed scanning {csv_path}")

def load_funded_addresses(file_path):
    """Load funded addresses from ``file_path`` applying ``normalize_address``
    to each line so comparisons are consistent."""
    with open(file_path, "r") as f:
        return set(normalize_address(line.strip()) for line in f.readlines())

def check_csv_against_addresses(csv_file, address_sets, recheck=False, safe_mode=False, pause_event=None, shutdown_event=None, start_row=0, state=None):
    """Scan ``csv_file`` for funded address matches.

    Returns a tuple ``(matches, completed)`` where ``completed`` indicates
    whether the file was fully processed without an interrupt/shutdown.
    """
    new_matches = set()
    all_matches = []
    filename = os.path.basename(csv_file)
    rows_scanned = 0
    start_time = time.perf_counter()
    completed = True  # [FIX PHASE 2] track if file processed fully
    set_metric("csv_checker.last_file", filename)
    set_metric("last_csv_checked_filename", filename)
    set_metric("csv_checker.last_timestamp", datetime.utcnow().isoformat())
    set_metric("csv_checker.rows_checked", 0)
    set_metric("csv_checker.matches_found", 0)

    funded_btc = address_sets.get('btc', {}) if isinstance(address_sets.get('btc'), dict) else {}
    funded_p2pkh = funded_btc.get('p2pkh', set())
    funded_p2sh = funded_btc.get('p2sh', set())
    funded_bech32 = funded_btc.get('bech32', set())

    if filename.endswith(".partial.csv"):
        return [], True
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        logger.error(f"❌ {filename} is empty or missing. Skipping.")
        return [], True

    logger.debug(f"Scanning {filename} | recheck={recheck} | start_row={start_row}")
    if start_row:
        logger.info(f"🔎 Resuming {filename} from row {start_row}...")
    else:
        logger.info(f"🔎 Checking {filename}...")
    if safe_mode:
        logger.warning("CSV checker running in safe mode – parser is more defensive and slower")

    try:
        with open(csv_file, newline="", encoding="utf-8", errors="replace") as f:
            logger.info(f"Opened {filename} for reading")
            reader = csv.DictReader(f)
            headers = reader.fieldnames

            if not headers:
                logger.error(f"❌ {filename} missing headers. Skipping file.")
                return []

            known = {c for cols in coin_columns.values() for c in cols}
            # These metadata fields are optional and may not be present in
            # every CSV. They are useful for tracing matches but are not
            # required for address comparisons.
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
                logger.warning(f"⚠️ Unknown columns in {filename}: {unknown}")

            for coin, columns in coin_columns.items():
                missing = [col for col in columns if col not in headers]
                if missing:
                    logger.warning(f"⚠️ {coin.upper()} columns missing in {filename}: {missing}")
                else:
                    logger.debug(f"🔎 {coin.upper()} columns scanned: {columns}")

            try:
                for row_num, row in enumerate(reader, start=1):
                    if shutdown_event and shutdown_event.is_set():
                        completed = False
                        break
                    if row_num <= start_row:
                        continue
                    if pause_event and pause_event.is_set():
                        while pause_event and pause_event.is_set():
                            time.sleep(0.2)
                        if pause_event.is_set():
                            continue
                    rows_scanned = row_num
                    increment_metric("csv_checker.rows_checked", 1)  # progress metric for dashboard
                    set_metric("csv_checker.rows_checked", rows_scanned)
                    if state and row_num % 1000 == 0:
                        # [FIX PHASE 2] persist progress periodically for crash resume
                        state.setdefault("files", {})[filename] = row_num
                        save_csv_state(state)
                    # Emit a heartbeat every 10k rows so long scans show activity
                    if rows_scanned % 10000 == 0:
                        logger.debug(f"[Progress] {filename}: {rows_scanned} rows scanned")
                    row_matches = []
                    try:
                        for coin, columns in coin_columns.items():
                            for col in columns:
                                raw = row.get(col)
                                addr = raw.strip() if raw else ""
                                normalized = normalize_address(addr)
                                if coin == 'btc':
                                    atype = detect_btc_address_type(normalized)
                                    if atype == 'p2pkh':
                                        in_funded = normalized in funded_p2pkh
                                    elif atype == 'p2sh':
                                        in_funded = normalized in funded_p2sh
                                    elif atype in ('p2wpkh', 'taproot'):
                                        in_funded = normalized in funded_bech32
                                    else:
                                        in_funded = False
                                else:
                                    atype = 'unknown'
                                    in_funded = normalized in address_sets.get(coin, set())
                                logger.debug(
                                    f"[Checker] {coin.upper()} column '{col}' -> '{normalized}' : {'MATCH' if in_funded else 'miss'}"
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
                                        if coin == 'btc':
                                            match_payload['addr_type'] = atype
                                            match_payload['witness_ver'] = '1' if atype == 'taproot' else ('0' if atype == 'p2wpkh' else 'N/A')

                                        balance = None
                                        if filename != "test_alerts.csv":
                                            balance = fetch_live_balance(addr, coin)
                                        if coin == 'btc':
                                            log_msg = (
                                                f"[{match_payload['timestamp']}] coin=BTC addr={addr} addr_type={atype} "
                                                f"witness_ver={match_payload['witness_ver']} balance={balance if balance is not None else 'unknown'} "
                                                f"file={filename} row={rows_scanned}"
                                            )
                                        else:
                                            log_msg = (
                                                f"[{match_payload['timestamp']}] coin={coin} address={addr} "
                                                f"balance={balance if balance is not None else 'unknown'} file={filename} row={rows_scanned}"
                                            )
                                        print(log_msg)
                                        logger.info(log_msg)

                                        if balance is not None:
                                            match_payload["balance"] = balance

                                        if ENABLE_PGP:
                                            try:
                                                encrypted = encrypt_with_pgp(json.dumps(match_payload), PGP_PUBLIC_KEY_PATH)
                                                alert_match(match_payload)
                                                alert_match({"encrypted": encrypted})
                                            except Exception as pgp_err:
                                                logger.warning(f"⚠️ PGP failed for {addr}: {pgp_err}")
                                                alert_match(match_payload)
                                        else:
                                            alert_match(match_payload)

                                        if normalized not in new_matches:
                                            new_matches.add(normalized)
                                            increment_metric("matched_keys", 1)
                                            increment_metric(f"matches_found_today.{coin}", 1)
                                            increment_metric(f"matches_found_lifetime.{coin}", 1)
                                            if coin == 'btc' and atype in {'p2pkh','p2sh','p2wpkh','taproot'}:
                                                increment_metric(f"matches_found_today.{atype}", 1)
                                                increment_metric(f"matches_found_lifetime.{atype}", 1)
                                            update_dashboard_stat("matches_found_lifetime", get_metric("matches_found_lifetime"))
                                        row_matches.append(addr)
                                        all_matches.append(match_payload)
                                        logger.debug("[STATUS] CSV Checker continuing without interruption")
                                    except Exception as match_err:
                                        logger.exception(f"Match processing error for {addr}: {match_err}")
                                        continue
                    except Exception as row_err:
                        # [FIX PHASE 2] one bad row should not abort entire scan
                        logger.exception(
                            f"Row skipped due to error in {csv_file}: {row_err}"
                        )
                        continue
                    if row_matches:
                        increment_metric("csv_checker.matches_found", len(row_matches))
                        set_metric("csv_checker.matches_found", get_metric("csv_checker.matches_found"))
            except csv.Error as e:
                logger.error(f"❌ CSV parsing error in {filename}: {e}")

        end_time = time.perf_counter()
        duration_sec = round(end_time - start_time, 2)
        CHECK_TIME_HISTORY.append(duration_sec)
        if len(CHECK_TIME_HISTORY) > MAX_HISTORY_SIZE:
            CHECK_TIME_HISTORY.pop(0)

        avg_time = round(sum(CHECK_TIME_HISTORY) / len(CHECK_TIME_HISTORY), 2)

        # Record that another CSV file has been processed both for the current
        # day and for lifetime statistics.
        increment_metric("csv_checked_today", 1)
        increment_metric("csv_checked_lifetime", 1)
        update_dashboard_stat("csv_checked_today", get_metric("csv_checked_today"))
        update_dashboard_stat("csv_checked_lifetime", get_metric("csv_checked_lifetime"))
        logger.info(f"CSV files checked today: {get_metric('csv_checked_today')}")
        if recheck:
            increment_metric("csv_rechecked_today", 1)
            increment_metric("csv_rechecked_lifetime", 1)
        update_dashboard_stat({
            "avg_check_time": avg_time,
            "last_check_duration": f"{duration_sec:.2f}s"
        })
        for coin in coin_columns:
            if address_sets.get(coin):
                # Update per-coin address counters to track scanning volume
                increment_metric(f"addresses_checked_today.{coin}", rows_scanned)
                increment_metric(f"addresses_checked_lifetime.{coin}", rows_scanned)
        update_dashboard_stat("addresses_checked_today", get_metric("addresses_checked_today"))
        update_dashboard_stat("addresses_checked_lifetime", get_metric("addresses_checked_lifetime"))

        logger.info(f"✅ {'Recheck' if recheck else 'Check'} complete: {len(new_matches)} matches found")
        logger.info(f"📄 {filename}: {rows_scanned:,} rows scanned | {len(new_matches)} unique matches | ⏱️ Time: {duration_sec:.2f}s")
        logger.info(
            json.dumps(
                {
                    "event": "csv_checked",
                    "file": filename,
                    "rows": rows_scanned,
                    "matches": len(new_matches),
                    "seconds": round(duration_sec, 2),
                }
            )
        )

        if state and completed and filename in state.get("files", {}):
            # [FIX PHASE 2] clear resume state only when file fully processed
            state["files"].pop(filename, None)
            save_csv_state(state)

        if new_matches:
            dest_path = os.path.join(MATCHED_CSV_DIR, filename)
            try:
                os.rename(csv_file, dest_path)
                logger.info(f"📂 Moved matched CSV to {dest_path}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to move {filename} to matched_csv/: {e}")

        return all_matches, completed

    except Exception as e:
        logger.exception(f"Error reading {filename}: {e}")
        return [], False

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
        set_metric("csv_checker", {"rows_checked": 0, "matches_found": 0, "last_file": ""})
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", True)
        logger.debug("🟢 CSV day-one checker started")
        logger.debug(f"Shared metrics initialized for {__name__}")
    except Exception as e:
        logger.exception(f"init_shared_metrics failed in {__name__}: {e}")

    address_sets = {}
    state = load_csv_state()
    state = load_csv_state()
    for coin, columns in coin_columns.items():
        full_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR, unique=False)
        if full_path:
            logger.info(f"🔎 Using funded list {os.path.basename(full_path)} for {coin.upper()}.")
            if coin == 'btc':
                address_sets[coin] = load_btc_funded_multi(full_path)
            else:
                address_sets[coin] = load_funded_addresses(full_path)
        else:
            logger.warning(f"⚠️ No funded list found for {coin.upper()} in DOWNLOADS_DIR")

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if shutdown_event and shutdown_event.is_set():
            break
        if filename.endswith(".partial.csv"):
            final = filename.replace(".partial.csv", ".csv")
            if os.path.exists(os.path.join(CSV_DIR, final)):
                logger.info(f"ℹ️ Skipping {filename} because final CSV already exists")
            continue
        if get_pause_event("csv_check") and get_pause_event("csv_check").is_set():
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, CHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        start_row = state.get("files", {}).get(filename, 0)
        matches, completed = check_csv_against_addresses(
            csv_path,
            address_sets,
            safe_mode=safe_mode,
            pause_event=pause_event,
            shutdown_event=shutdown_event,
            start_row=start_row,
            state=state,
        )
        # [FIX PHASE 2] only mark file when fully processed
        if completed and not (shutdown_event and shutdown_event.is_set()):
            mark_csv_as_checked(filename, CHECKED_CSV_LOG)
        if shutdown_event and shutdown_event.is_set():
            break

    update_csv_eta()
    set_metric("status.csv_check", "Stopped")
    try:
        from core.dashboard import set_thread_health
        set_thread_health("csv_check", False)
    except Exception:
        logger.warning("Failed to update csv_check thread health", exc_info=True)


def check_csvs(shared_metrics=None, shutdown_event=None, pause_event=None, safe_mode=False, log_q=None):
    initialize_logging(log_q)
    try:
        init_shared_metrics(shared_metrics)
        from core.dashboard import register_control_events
        register_control_events(shutdown_event, pause_event, module="csv_recheck")
        set_metric("status.csv_recheck", "Running")
        set_metric("csv_rechecked_today", 0)
        set_metric("addresses_checked_today", {c: 0 for c in coin_columns})
        set_metric("csv_checker", {"rows_checked": 0, "matches_found": 0, "last_file": ""})
        from core.dashboard import set_thread_health
        set_thread_health("csv_recheck", True)
        logger.debug("🟢 CSV recheck checker started")
        logger.debug(f"Shared metrics initialized for {__name__}")
    except Exception as e:
        logger.exception(f"init_shared_metrics failed in {__name__}: {e}")

    address_sets = {}
    state = load_csv_state()
    for coin, columns in coin_columns.items():
        unique_path = find_latest_funded_file(coin, directory=DOWNLOADS_DIR, unique=True)
        if unique_path:
            logger.info(f"🔎 Using unique list {os.path.basename(unique_path)} for {coin.upper()}.")
            if coin == 'btc':
                address_sets[coin] = load_btc_funded_multi(unique_path)
            else:
                address_sets[coin] = load_funded_addresses(unique_path)
        else:
            logger.warning(f"⚠️ No unique list found for {coin.upper()} in DOWNLOADS_DIR")

    from core.dashboard import get_pause_event
    for filename in os.listdir(CSV_DIR):
        if shutdown_event and shutdown_event.is_set():
            break
        if filename.endswith(".partial.csv"):
            final = filename.replace(".partial.csv", ".csv")
            if os.path.exists(os.path.join(CSV_DIR, final)):
                logger.info(f"ℹ️ Skipping {filename} because final CSV already exists")
            continue
        if get_pause_event("csv_recheck") and get_pause_event("csv_recheck").is_set():
            time.sleep(1)
            continue
        if not filename.endswith(".csv") or has_been_checked(filename, RECHECKED_CSV_LOG):
            continue
        csv_path = os.path.join(CSV_DIR, filename)
        start_row = state.get("files", {}).get(filename, 0)
        matches, completed = check_csv_against_addresses(
            csv_path,
            address_sets,
            recheck=True,
            safe_mode=safe_mode,
            pause_event=pause_event,
            shutdown_event=shutdown_event,
            start_row=start_row,
            state=state,
        )
        # [FIX PHASE 2] only mark file when fully processed
        if completed and not (shutdown_event and shutdown_event.is_set()):
            mark_csv_as_checked(filename, RECHECKED_CSV_LOG)
        if shutdown_event and shutdown_event.is_set():
            break

    update_csv_eta()
    set_metric("status.csv_recheck", "Stopped")
    try:
        from core.dashboard import set_thread_health
        set_thread_health("csv_recheck", False)
    except Exception:
        logger.warning("Failed to update csv_recheck thread health", exc_info=True)

def inject_test_match(test_address="1KFHE7w8BhaENAswwryaoccDb6qcT6DbYY"):
    test_csv = os.path.join(CSV_DIR, "test_match.csv")
    with open(test_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["btc_U", "wif"])
        writer.writeheader()
        writer.writerow({"btc_U": test_address, "wif": "TESTWIF"})

    matches, _ = check_csv_against_addresses(test_csv, {"btc": {test_address}}, safe_mode=True)
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
