# main.py

import os
import io
import time
import sys
import signal
import argparse
import multiprocessing
import threading
from datetime import datetime, timedelta
from multiprocessing import Process, set_start_method
import psutil

# Wrap stdout once with UTF-8 encoding if not already wrapped
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding='utf-8',
        errors='replace',
        line_buffering=True
    )

try:
    import GPUtil
except ImportError:
    GPUtil = None

try:
    import pyopencl as cl
except ImportError:
    cl = None

# Track disk free space to estimate fill ETA
_last_disk_check = (time.time(), psutil.disk_usage('/').free)
# Track backlog processing for ETA calculations
_backlog_total_time = 0.0
_backlog_processed = 0
_backlog_last_ts = time.time()
_last_csv_created = 0

from config.settings import (
    ENABLE_CHECKPOINT_RESTORE, CHECKPOINT_INTERVAL_SECONDS,
    LOGO_ART, ENABLE_DAY_ONE_CHECK, ENABLE_UNIQUE_RECHECK,
    ENABLE_DASHBOARD, ENABLE_KEYGEN, ENABLE_ALERTS,
    ENABLE_BACKLOG_CONVERSION, LOG_DIR, CONFIG_FILE_PATH,
    CSV_DIR, VANITYSEARCH_PATH, DOWNLOAD_DIR
)

from core.logger import log_message
from core.checkpoint import load_keygen_checkpoint, save_keygen_checkpoint
from core.downloader import download_and_compare_address_lists, generate_test_csv
from core.csv_checker import check_csvs_day_one, check_csvs
from core.alerts import trigger_startup_alerts, alert_match
from core.dashboard import (
    update_dashboard_stat,
    _default_metrics,
    init_shared_metrics,
    init_dashboard_manager,
    get_current_metrics,
)
from ui.dashboard_gui import start_dashboard
from core.gpu_selector import assign_gpu_roles
from core.altcoin_derive import start_altcoin_conversion_process  # <-- updated import


def display_logo():
    print(LOGO_ART, flush=True)
    print("\nIf you like this software...donate!\n", flush=True)
    print("BTC: 18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y", flush=True)
    print("LTC: LNmgLkonXtecopmGauqsDFvci4XQTZAWmg", flush=True)
    print("DOGE: DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5", flush=True)
    print("XMR: 43DUJ1MA7Mv1n4BTRHemEbDmvYzMysVt2djHnjGzrHZBb4WgMDtQHWh51ZfbcVwHP8We6pML4f1Q7SNEtveYCk4HDdb14ik", flush=True)
    print("ETH: 0xCb8B2937D60c47438562A2E53d08B85865B57741", flush=True)
    print("PEP: PbCiPTNrYaCgv1aqNCds5n7Q73znGrTkgp\n", flush=True)


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
from core.gpu_selector import (
    get_vanitysearch_gpu_ids,
    get_altcoin_gpu_ids,
    get_gpu_assignments,
)


def metrics_updater(shared_metrics=None):
    try:
        init_shared_metrics(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)
    global _last_disk_check, _backlog_total_time, _backlog_processed, _backlog_last_ts, _last_csv_created
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

            vm = psutil.virtual_memory()
            ram_percent = vm.percent
            stats = {
                'cpu_usage': f"{psutil.cpu_percent()}%",
                'ram_usage': f"{vm.used / (1024 ** 3):.1f} GB / {vm.total / (1024 ** 3):.1f} GB ({ram_percent}%)",
                'disk_free_gb': round(disk_free / (1024 ** 3), 2),
                'disk_fill_eta': disk_eta,
                'gpu_stats': {},
                'gpu_assignments': get_gpu_assignments(),
            }
            vs_ids = set(get_vanitysearch_gpu_ids())
            ad_ids = set(get_altcoin_gpu_ids())

            if GPUtil:
                try:
                    gpus = GPUtil.getGPUs()
                    for gpu in gpus:
                        try:
                            usage = f"{gpu.load * 100:.0f}%"
                            vram = f"{gpu.memoryUsed/1024:.1f}GB / {gpu.memoryTotal/1024:.1f}GB"
                        except Exception:
                            usage = "N/A"
                            vram = "Unavailable"
                        name = gpu.name
                        if gpu.id in vs_ids:
                            name += " (VS)"
                        if gpu.id in ad_ids:
                            name += " (AD)"
                        if usage in ["N/A", None]:
                            usage = "Active (No Stats)" if gpu.id in ad_ids | vs_ids else "N/A"
                        stats['gpu_stats'][f"GPU{gpu.id}"] = {
                            'name': name,
                            'usage': usage,
                            'vram': vram,
                            'temp': f"{gpu.temperature}°C" if hasattr(gpu, 'temperature') else 'N/A',
                        }
                except Exception as e:
                    log_message(f"⚠️ GPU read failed: {e}", "WARNING")

            next_id = len(stats['gpu_stats'])
            if cl:
                try:
                    for platform in cl.get_platforms():
                        for device in platform.get_devices():
                            already = any(
                                info.get('name', '').startswith(device.name)
                                for info in stats['gpu_stats'].values()
                            )
                            if already:
                                continue
                            name = device.name
                            roles = []
                            if next_id in vs_ids:
                                roles.append('VS')
                            if next_id in ad_ids:
                                roles.append('AD')
                            if roles:
                                name += " (" + "/".join(roles) + ")"
                            usage = 'Active (No Stats)' if roles else 'N/A'
                            stats['gpu_stats'][f"GPU{next_id}"] = {
                                'name': name,
                                'usage': usage,
                                'vram': 'Unavailable',
                                'temp': 'N/A',
                            }
                            next_id += 1
                except Exception as e:
                    log_message(f"⚠️ OpenCL GPU read failed: {e}", "WARNING")

            # ----- Backlog ETA Calculation -----
            metrics_snapshot = get_current_metrics()
            queue_count = metrics_snapshot.get('backlog_files_queued', 0)
            created_today = metrics_snapshot.get('csv_created_today', 0)
            if created_today > _last_csv_created:
                _backlog_total_time += now - _backlog_last_ts
                _backlog_processed += created_today - _last_csv_created
                _backlog_last_ts = now
                _last_csv_created = created_today

            if _backlog_processed > 0:
                avg_time = _backlog_total_time / _backlog_processed
                stats['backlog_avg_time'] = f"{avg_time:.2f}s"
                if queue_count > 0:
                    eta_sec = avg_time * queue_count
                    stats['backlog_eta'] = str(timedelta(seconds=int(eta_sec)))
                else:
                    stats['backlog_eta'] = 'N/A'
            else:
                stats['backlog_eta'] = 'N/A'

            prog = keygen_progress()
            stats['keys_generated_lifetime'] = prog['total_keys_generated']
            stats['uptime'] = prog['elapsed_time']
            stats['last_updated'] = datetime.utcnow().strftime('%H:%M:%S')
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
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)
    processes = []
    named_processes = []

    if ENABLE_CHECKPOINT_RESTORE:
        load_keygen_checkpoint()
        log_message("🧠 Checkpoint restore enabled.", "INFO")

    if not args.skip_downloads:
        if should_skip_download_today(DOWNLOAD_DIR):
            log_message("🚩 Skipping address downloads — already downloaded today.")
        else:
            log_message("🌐 Downloading address lists...")
            download_and_compare_address_lists()
    else:
        # Ensure test CSV exists even when downloads are skipped
        generate_test_csv()

    if ENABLE_KEYGEN and not args.headless:
        try:
            p = Process(target=start_keygen_loop, args=(shared_metrics,))
            p.daemon = True
            p.start()
            log_message("[Started] Keygen subprocess", "INFO")
            processes.append(p)
            named_processes.append(("keygen", p))
        except Exception as e:
            log_message(f"❌ Failed to launch keygen: {e}", "ERROR")

    if ENABLE_DAY_ONE_CHECK:
        try:
            p = Process(target=check_csvs_day_one, args=(shared_metrics,))
            p.daemon = True
            p.start()
            log_message("[Started] Day One CSV checker", "INFO")
            processes.append(p)
            named_processes.append(("csv_check", p))
        except Exception as e:
            log_message(f"❌ Failed to start day-one checker: {e}", "ERROR")

    if ENABLE_UNIQUE_RECHECK:
        try:
            p = Process(target=check_csvs, args=(shared_metrics,))
            p.daemon = True
            p.start()
            log_message("[Started] Unique recheck", "INFO")
            processes.append(p)
            named_processes.append(("csv_recheck", p))
        except Exception as e:
            log_message(f"❌ Failed to start recheck: {e}", "ERROR")

    if ENABLE_BACKLOG_CONVERSION and not args.skip_backlog:
        try:
            p = start_altcoin_conversion_process(shutdown_event, shared_metrics)
            log_message("[Started] Altcoin derive subprocess", "INFO")
            processes.append(p)
            named_processes.append(("altcoin", p))
        except Exception as e:
            log_message(f"❌ Failed to start altcoin convert: {e}", "ERROR")

    if ENABLE_ALERTS:
        try:
            p = Process(target=trigger_startup_alerts)
            p.daemon = True
            p.start()
            log_message("[Started] Startup alerts", "INFO")
            processes.append(p)
            named_processes.append(("alerts", p))
        except Exception as e:
            log_message(f"❌ Failed to trigger startup alerts: {e}", "ERROR")

    if CHECKPOINT_INTERVAL_SECONDS:
        try:
            p = Process(target=save_checkpoint_loop)
            p.daemon = True
            p.start()
            log_message("[Started] Checkpoint saver", "INFO")
            processes.append(p)
            named_processes.append(("checkpoint", p))
        except Exception as e:
            log_message(f"❌ Failed to start checkpoint saver: {e}", "ERROR")

    try:
        p = Process(target=metrics_updater, args=(shared_metrics,))
        p.daemon = True
        p.start()
        log_message("[Started] Metrics updater", "INFO")
        processes.append(p)
        named_processes.append(("metrics", p))
    except Exception as e:
        log_message(f"❌ Failed to launch metrics updater: {e}", "ERROR")

    return processes, named_processes


def run_allinkeys(args):
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    os.environ.setdefault("PYOPENCL_COMPILER_OUTPUT", "1")
    display_logo()

    assign_gpu_roles()
    test_csv = os.path.join(DOWNLOAD_DIR, "test_alerts.csv")
    if not os.path.exists(test_csv):
        generate_test_csv()
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
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] init_shared_metrics failed in {__name__}: {e}", flush=True)

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

    processes, named_processes = run_all_processes(args, shutdown_event, shared_metrics)

    def monitor():
        while not shutdown_event.is_set():
            status = {name: proc.is_alive() for name, proc in named_processes}
            update_dashboard_stat("thread_health_flags", status)
            time.sleep(2)

    threading.Thread(target=monitor, daemon=True).start()

    def shutdown_handler(sig, frame):
        print("\n🛑 Ctrl+C received. Shutting down gracefully...", flush=True)
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
        print(f"✅ VanitySearch found: {VANITYSEARCH_PATH}", flush=True)

    parser = argparse.ArgumentParser(description="AllInKeys Modular Runner")
    parser.add_argument("--skip-backlog", action="store_true", help="Skip backlog conversion on startup")
    parser.add_argument("--no-dashboard", action="store_true", help="Don't launch GUI dashboard")
    parser.add_argument("--skip-downloads", action="store_true", help="Skip downloading balance files")
    parser.add_argument("--headless", action="store_true", help="Run without any GUI or visuals")
    parser.add_argument("--match-test", action="store_true", help="Trigger fake match alert on startup")
    args = parser.parse_args()

    run_allinkeys(args)
