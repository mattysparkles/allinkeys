"""
csv_checker.py – 📁 CSV scanning against complete list of funded address updated daily
"""

import os
import csv
from datetime import datetime
from config.settings import (
    CSV_DIR, UNIQUE_DIR, FULL_DIR, CHECKED_CSV_LOG, RECHECKED_CSV_LOG,
    ENABLE_ALERTS, ENABLE_PGP, PGP_PUBLIC_KEY_PATH
)
from config.coin_definitions import coin_columns
from core.alerts import alert_match
from core.logger import log_message
from utils.pgp_utils import encrypt_with_pgp

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
    new_matches = []
    filename = os.path.basename(csv_file)
    check_type = "Recheck" if recheck else "First Check"

    try:
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for coin, columns in coin_columns.items():
                    for col in columns:
                        addr = row.get(col, "").strip()
                        if addr and addr in address_set:
                            log_message(f"✅ MATCH in {filename}: {addr} ({coin})", "ALERT")

                            match_payload = {
                                "timestamp": datetime.utcnow().isoformat(),
                                "coin": coin,
                                "address": addr,
                                "csv_file": filename,
                                "privkey": row.get("wif", row.get("priv_hex", ""))
                            }

                            if ENABLE_PGP:
                                encrypted = encrypt_with_pgp(str(match_payload), PGP_PUBLIC_KEY_PATH)
                                alert_match({"encrypted": encrypted})
                            else:
                                alert_match(match_payload)

                            new_matches.append(addr)

        return new_matches

    except Exception as e:
        log_message(f"❌ Error processing {filename}: {str(e)}", "ERROR")
        return []

def check_csvs_day_one():
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

def check_csvs():
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
