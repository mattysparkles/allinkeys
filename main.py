# main.py

import os
import io
import time
import sys
import signal
import argparse
import multiprocessing
from datetime import datetime
from multiprocessing import Process, set_start_method
import psutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import GPUtil
except ImportError:
    GPUtil = None

# Track disk free space to estimate fill ETA
_last_disk_check = (time.time(), psutil.disk_usage('/').free)

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
from core.dashboard import (
    update_dashboard_stat,
    _default_metrics,
    init_shared_metrics,
    init_dashboard_manager,
)
from ui.dashboard_gui import start_dashboard
from core.gpu_selector import assign_gpu_roles
from core.altcoin_derive import start_altcoin_conversion_process  # <-- updated import


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


from core.dashboard import init_shared_metrics


def metrics_updater(shared_metrics=None):
    try:
        init_shared_metrics(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}")
    global _last_disk_check
    while True:
        try:
            from core.keygen import keygen_progress
            now = time.time()
            disk_free = psutil.disk_usage('/').free
            prev_t, prev_free = _last_disk_check
            _last_disk_check = (now, disk_free)
            rate = (prev_free - disk_free) / max(1, now - prev_t)
            if rate > 0:
                eta_sec = disk_free / rate
                hrs = int(eta_sec // 3600)
                mins = int((eta_sec % 3600) // 60)
                secs = int(eta_sec % 60)
                disk_eta = f"{hrs:02}:{mins:02}:{secs:02}"
            else:
                disk_eta = "N/A"

            stats = {
                'cpu_usage_percent': psutil.cpu_percent(),
                'ram_usage_gb': round(psutil.virtual_memory().used / (1024 ** 3), 2),
                'disk_free_gb': round(disk_free / (1024 ** 3), 2),
                'disk_fill_eta': disk_eta,
            }
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
            stats['keys_generated_lifetime'] = prog['total_keys_generated']
            stats['uptime'] = prog['elapsed_time']
            try:
                from config.settings import BATCH_SIZE
                stats['vanity_progress_percent'] = round(
                    (prog.get('index_within_batch', 0) / float(BATCH_SIZE)) * 100,
                    2,
                )
            except Exception:
                stats['vanity_progress_percent'] = 0
            update_dashboard_stat(stats)
            log_message(f"📊 Metrics updated: {stats}", "DEBUG")
        except Exception as e:
            log_message(f"❌ Error in metrics updater: {e}", "ERROR")
        time.sleep(3)


def should_skip_download_today(download_dir):
    today_str = datetime.now().strftime("%Y-%m-%d")
    return any(today_str in f for f in os.listdir(download_dir) if f.endswith(".txt"))


def run_all_processes(args, shutdown_event, shared_metrics):
    from core.keygen import start_keygen_loop
    from core.backlog import start_backlog_conversion_loop  # Optional non-GPU parser
    from core.dashboard import init_shared_metrics

    try:
        init_shared_metrics(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}")
    processes = []

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
        p = Process(target=start_keygen_loop, args=(shared_metrics,))
        p.start()
        processes.append(p)
        log_message("🧬 Keygen loop started.", "INFO")

    if ENABLE_DAY_ONE_CHECK:
        p = Process(target=check_csvs_day_one, args=(shared_metrics,))
        p.start()
        processes.append(p)
        log_message("🧾 Day One CSV check scheduled.", "INFO")

    if ENABLE_UNIQUE_RECHECK:
        p = Process(target=check_csvs, args=(shared_metrics,))
        p.start()
        processes.append(p)
        log_message("🔁 Unique recheck scheduled.", "INFO")

    if ENABLE_BACKLOG_CONVERSION and not args.skip_backlog:
        p = start_altcoin_conversion_process(shutdown_event, shared_metrics)
        processes.append(p)
        log_message("📁 Altcoin conversion loop scheduled.", "INFO")

    if ENABLE_ALERTS:
        p = Process(target=trigger_startup_alerts)
        p.start()
        processes.append(p)
        log_message("🚨 Alert system primed.", "INFO")

    if CHECKPOINT_INTERVAL_SECONDS:
        p = Process(target=save_checkpoint_loop)
        p.start()
        processes.append(p)
        log_message("🕒 Checkpoint thread started.", "INFO")

    p = Process(target=metrics_updater, args=(shared_metrics,))
    p.start()
    processes.append(p)
    log_message("📈 Metrics updater thread launched.")

    return processes


def run_allinkeys(args):
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    display_logo()

    assign_gpu_roles()
    shutdown_event = multiprocessing.Event()

    # Use dashboard's helper to create a Manager-backed shared metrics dict with
    # an associated lock.  Previously this file manually created its own
    # ``Manager`` without initializing ``metrics_lock`` which caused
    # ``get_current_metrics()`` to return an empty dict.  By delegating to
    # :func:`init_dashboard_manager` we ensure the lock and defaults are set up
    # correctly for all subprocesses.
    shared_metrics = init_dashboard_manager()
    try:
        init_shared_metrics(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}")

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

    processes = run_all_processes(args, shutdown_event, shared_metrics)

    def shutdown_handler(sig, frame):
        print("\n🛑 Ctrl+C received. Shutting down gracefully...")
        shutdown_event.set()
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)

    if ENABLE_DASHBOARD and not args.no_dashboard:
        start_dashboard()
    else:
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
