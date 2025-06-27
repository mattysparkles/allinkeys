# main.py

import os
import time
import argparse
from datetime import datetime
from multiprocessing import Process, set_start_method
import psutil

try:
    import GPUtil
except ImportError:
    GPUtil = None

from config.settings import (
    ENABLE_CHECKPOINT_RESTORE, CHECKPOINT_INTERVAL_SECONDS,
    LOGO_ART, ENABLE_DAY_ONE_CHECK, ENABLE_UNIQUE_RECHECK,
    ENABLE_DASHBOARD, ENABLE_KEYGEN, ENABLE_ALERTS,
    ENABLE_BACKLOG_CONVERSION, LOG_DIR, CONFIG_FILE_PATH,
    CSV_DIR, VANITYSEARCH_PATH, DOWNLOAD_DIR
)

from core.logger import log_message
from core.checkpoint import load_keygen_checkpoint, save_keygen_checkpoint
from core.downloader import download_and_compare_address_lists
from core.csv_checker import check_csvs_day_one, check_csvs
from core.alerts import trigger_startup_alerts, alert_match
from core.dashboard import update_dashboard_stat
from ui.dashboard_gui import start_dashboard
from core.gpu_selector import assign_gpu_roles, assigned_gpus


def display_logo():
    print(LOGO_ART)
    print("\nIf you like this software...donate!\n")
    print("BTC: 18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y")
    print("LTC: LNmgLkonXtecopmGauqsDFvci4XQTZAWmg")
    print("DOGE: DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5")
    print("XMR: 43DUJ1MA7Mv1n4BTRHemEbDmvYzMysVt2djHnjGzrHZBb4WgMDtQHWh51ZfbcVwHP8We6pML4f1Q7SNEtveYCk4HDdb14ik")
    print("ETH: 0xCb8B2937D60c47438562A2E53d08B85865B57741")
    print("PEP: PbCiPTNrYaCgv1aqNCds5n7Q73znGrTkgp\n")


def save_checkpoint_loop():
    while True:
        try:
            from core.keygen import keygen_progress
            save_keygen_checkpoint(keygen_progress())
            log_message("💾 Checkpoint saved.", "DEBUG")
        except Exception as e:
            log_message(f"❌ Error in checkpoint save loop: {e}", "ERROR")
        time.sleep(CHECKPOINT_INTERVAL_SECONDS)


def metrics_updater():
    while True:
        try:
            from core.keygen import keygen_progress
            stats = {}
            stats['cpu'] = psutil.cpu_percent()
            stats['ram'] = psutil.virtual_memory().percent
            stats['disk'] = psutil.disk_usage('/').percent

            if GPUtil:
                try:
                    gpus = GPUtil.getGPUs()
                    stats['gpu'] = gpus[0].load * 100 if gpus else 0
                except Exception as e:
                    stats['gpu'] = 0
                    log_message(f"⚠️ GPU read failed: {e}", "WARNING")
            else:
                stats['gpu'] = 0

            prog = keygen_progress()
            stats['keyrate'] = prog['total_keys_generated']
            stats['uptime'] = prog['elapsed_time']
            update_dashboard_stat(stats)

            log_message(f"📊 Metrics updated: {stats}", "DEBUG")
        except Exception as e:
            log_message(f"❌ Error in metrics updater: {e}", "ERROR")
        time.sleep(3)


def should_skip_download_today(download_dir):
    today_str = datetime.now().strftime("%Y-%m-%d")
    return any(today_str in f for f in os.listdir(download_dir) if f.endswith(".txt"))


def run_all_processes(args):
    # 💻 Defer GPU-dependent imports until after GPUs are assigned
    from core.keygen import start_keygen_loop
    from core.backlog import start_backlog_conversion_loop

    if ENABLE_CHECKPOINT_RESTORE:
        load_keygen_checkpoint()
        log_message("🧠 Checkpoint restore enabled.", "INFO")

    if not args.skip_downloads:
        if should_skip_download_today(DOWNLOAD_DIR):
            log_message("🚩 Skipping address downloads — already downloaded today.")
        else:
            log_message("🌐 Downloading address lists...")
            download_and_compare_address_lists()

    if ENABLE_KEYGEN and not args.headless:
        Process(target=start_keygen_loop).start()
        log_message("🧬 Keygen loop started.", "INFO")

    if ENABLE_DAY_ONE_CHECK:
        Process(target=check_csvs_day_one).start()
        log_message("🧾 Day One CSV check scheduled.", "INFO")

    if ENABLE_UNIQUE_RECHECK:
        Process(target=check_csvs).start()
        log_message("🔁 Unique recheck scheduled.", "INFO")

    if ENABLE_BACKLOG_CONVERSION and not args.skip_backlog:
        Process(target=start_backlog_conversion_loop).start()
        log_message("📁 Backlog conversion loop scheduled.", "INFO")

    if ENABLE_ALERTS:
        Process(target=trigger_startup_alerts).start()
        log_message("🚨 Alert system primed.", "INFO")

    if CHECKPOINT_INTERVAL_SECONDS:
        Process(target=save_checkpoint_loop).start()
        log_message("🕒 Checkpoint thread started.", "INFO")

    Process(target=metrics_updater).start()
    log_message("📈 Metrics updater thread launched.")


def run_allinkeys(args):
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    display_logo()

    # ✅ GPU selection BEFORE everything else
    assign_gpu_roles()

    if args.match_test:
        test_data = {
            "seed": "TESTSEED123",
            "btc_U": "1TestAddressUncompressed",
            "btc_C": "1TestAddressCompressed",
            "source_file": "test_static_file.csv",
            "timestamp": datetime.utcnow().isoformat(),
            "test_mode": True
        }
        log_message("🧺 Running simulated match alert...")
        alert_match(test_data, test_mode=True)

    if ENABLE_DASHBOARD and not args.no_dashboard:
        Process(target=run_all_processes, args=(args,)).start()
        start_dashboard()
    else:
        run_all_processes(args)

    while True:
        time.sleep(10)


if __name__ == "__main__":
    set_start_method("spawn")

    if not os.path.exists(VANITYSEARCH_PATH):
        raise FileNotFoundError(f"VanitySearch not found at: {VANITYSEARCH_PATH}")
    else:
        print(f"✅ VanitySearch found: {VANITYSEARCH_PATH}")

    parser = argparse.ArgumentParser(description="AllInKeys Modular Runner")
    parser.add_argument("--skip-backlog", action="store_true", help="Skip backlog conversion on startup")
    parser.add_argument("--no-dashboard", action="store_true", help="Don't launch GUI dashboard")
    parser.add_argument("--skip-downloads", action="store_true", help="Skip downloading balance files")
    parser.add_argument("--headless", action="store_true", help="Run without any GUI or visuals")
    parser.add_argument("--match-test", action="store_true", help="Trigger fake match alert on startup")
    args = parser.parse_args()

    run_allinkeys(args)
