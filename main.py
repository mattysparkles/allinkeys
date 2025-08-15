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
from multiprocessing import Process
from core.logger import get_logger
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

import config.settings as settings
from config.settings import (
    ENABLE_CHECKPOINT_RESTORE, CHECKPOINT_INTERVAL_SECONDS,
    LOGO_ART, ENABLE_DAY_ONE_CHECK, ENABLE_UNIQUE_RECHECK,
    ENABLE_DASHBOARD, ENABLE_KEYGEN, ENABLE_ALERTS,
    ENABLE_BACKLOG_CONVERSION, LOG_DIR, CONFIG_FILE_PATH,
    CSV_DIR, VANITYSEARCH_PATH, DOWNLOAD_DIR, VANITY_OUTPUT_DIR,
    CHECKER_BACKLOG_PAUSE_THRESHOLD
)

from core.logger import log_message, start_listener, stop_listener, get_logger
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
    get_metric,
    set_metric,
    warn_throttled,
)
import core.dashboard as dashboard_core
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
    from core.worker_bootstrap import ensure_metrics_ready, _safe_set_metric, _safe_inc_metric
    try:
        ensure_metrics_ready()
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] ensure_metrics_ready failed in {__name__}: {e}", flush=True)
    global _last_disk_check, _backlog_total_time, _backlog_processed, _backlog_last_ts, _last_csv_created
    kps_start_time = time.time()
    kps_start_keys = get_metric('keys_generated_today', 0)
    while True:
        try:
            from core.dashboard import reset_daily_metrics_if_needed
            reset_daily_metrics_if_needed()
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
            curr_today = get_metric('keys_generated_today', 0)
            curr_lifetime = get_metric('keys_generated_lifetime', 0)
            elapsed = max(1, now - kps_start_time)
            keys_per_sec = (curr_today - kps_start_keys) / elapsed
            stats['keys_generated_lifetime'] = curr_lifetime
            stats['keys_per_sec'] = round(keys_per_sec, 2)
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


def run_all_processes(args, shutdown_events, shared_metrics, pause_events, log_q):
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

    from core.gpu_scheduler import start_scheduler
    gpu_sched, vanity_gpu_flag, altcoin_gpu_flag, assignment_flag = start_scheduler(
        shared_metrics, shutdown_events.get('keygen')
    )
    processes.append(gpu_sched)
    named_processes.append(("gpu_scheduler", gpu_sched))

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

    backlog_files = []
    try:
        backlog_files = [
            f for f in os.listdir(VANITY_OUTPUT_DIR) if f.endswith(".txt")
        ]
    except Exception:
        pass

    # Determine current GPU strategy from shared metrics with a safe fallback
    gpu_strategy = "manual"
    try:
        gpu_strategy = shared_metrics.get("gpu_strategy", "manual")
    except Exception:
        try:
            gpu_strategy = get_current_metrics().get("gpu_strategy", "manual")
        except Exception:
            pass

    skip_vanity = gpu_strategy == "swing" and len(backlog_files) >= 100
    if skip_vanity:
        log_message(
            "[Startup] Detected backlog of 100+ files; delaying VanitySearch.",
            "INFO",
        )
        set_metric("status.keygen", "Stopped")
        vanity_gpu_flag.value = 0
        altcoin_gpu_flag.value = 1
        assignment_flag.value = 1
        set_metric("vanity_gpu_on", False)
        set_metric("altcoin_gpu_on", True)
        set_metric("gpu_assignment", "altcoin")
    elif ENABLE_KEYGEN and not args.headless:
        try:
            p = Process(target=start_keygen_loop, args=(shared_metrics, shutdown_events.get('keygen'), pause_events.get('keygen'), vanity_gpu_flag))
            p.daemon = True
            p.start()
            log_message("[Started] Keygen subprocess", "INFO")
            processes.append(p)
            named_processes.append(("keygen", p))
        except Exception as e:
            log_message(f"❌ Failed to launch keygen: {e}", "ERROR")

    if ENABLE_DAY_ONE_CHECK:
        try:
            p = Process(target=check_csvs_day_one, args=(shared_metrics, shutdown_events.get('csv_check'), pause_events.get('csv_check'), False, log_q))
            p.daemon = True
            p.start()
            log_message("[Started] Day One CSV checker", "INFO")
            processes.append(p)
            named_processes.append(("csv_check", p))
        except Exception as e:
            log_message(f"❌ Failed to start day-one checker: {e}", "ERROR")

    if ENABLE_UNIQUE_RECHECK:
        try:
            p = Process(target=check_csvs, args=(shared_metrics, shutdown_events.get('csv_recheck'), pause_events.get('csv_recheck'), False, log_q))
            p.daemon = True
            p.start()
            log_message("[Started] Unique recheck", "INFO")
            processes.append(p)
            named_processes.append(("csv_recheck", p))
        except Exception as e:
            log_message(f"❌ Failed to start recheck: {e}", "ERROR")

    if ENABLE_BACKLOG_CONVERSION and not args.skip_backlog:
        try:
            p = start_altcoin_conversion_process(shutdown_events.get('altcoin'), shared_metrics, pause_events.get('altcoin'), log_q, altcoin_gpu_flag)
            log_message("[Started] Altcoin derive subprocess", "INFO")
            processes.append(p)
            named_processes.append(("altcoin", p))
        except Exception as e:
            log_message(f"❌ Failed to start altcoin convert: {e}", "ERROR")

    if ENABLE_ALERTS:
        try:
            p = Process(target=trigger_startup_alerts, args=(shared_metrics,))
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


def run_btc_only(args):
    """Run BTC-only keygen and checker pipeline."""
    from multiprocessing import Process
    # Allow command line toggle to enable bc1 address generation.  The
    # settings module defines bech32 modes as disabled by default for
    # backwards compatibility.
    if getattr(args, "enable_bc1", False):
        settings.ENABLE_P2WPKH = True
        settings.ENABLE_TAPROOT = True
    from core.keygen import start_keygen_loop
    from core.btc_only_checker import (
        prepare_btc_only_mode,
        process_pending_vanity_outputs_once,
        get_vanity_backlog_count,
    )

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(VANITY_OUTPUT_DIR, exist_ok=True)
    start_listener()
    display_logo()
    assign_gpu_roles()

    shared_metrics = init_dashboard_manager()
    init_shared_metrics(shared_metrics)

    shutdown_event = multiprocessing.Event()
    keygen_shutdown = multiprocessing.Event()
    keygen_pause = multiprocessing.Event()

    from core.dashboard import register_control_events, get_pause_event, get_shutdown_event
    register_control_events(shutdown_event, None)
    register_control_events(keygen_shutdown, keygen_pause, module="keygen")
    keygen_pause = get_pause_event("keygen")
    keygen_shutdown = get_shutdown_event("keygen")

    logger = get_logger("btc_only")
    use_all = bool(args.all)
    use_funded = bool(args.funded)
    if not (use_all ^ use_funded):
        log_message("Must specify exactly one of -all or -funded", "ERROR")
        return

    prepare_btc_only_mode(use_all, logger, skip_downloads=args.skip_downloads)

    keygen_proc = Process(target=start_keygen_loop, args=(shared_metrics, keygen_shutdown, keygen_pause, None))
    keygen_proc.daemon = True
    keygen_proc.start()

    metrics_proc = Process(target=metrics_updater, args=(shared_metrics,))
    metrics_proc.daemon = True
    metrics_proc.start()

    if ENABLE_DASHBOARD and not args.no_dashboard:
        dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
        dashboard_thread.start()

    above = below = 0
    try:
        while not shutdown_event.is_set():
            backlog = get_vanity_backlog_count()
            set_metric("vanity_backlog_count", backlog)
            if backlog > CHECKER_BACKLOG_PAUSE_THRESHOLD:
                above += 1
                below = 0
                if above >= 2 and not keygen_pause.is_set():
                    keygen_pause.set()
                    warn_throttled(
                        "backlog_pause",
                        f"Paused keygen: backlog {backlog} > {CHECKER_BACKLOG_PAUSE_THRESHOLD}",
                    )
            else:
                below += 1
                above = 0
                if below >= 2 and keygen_pause.is_set():
                    keygen_pause.clear()
                    warn_throttled(
                        "backlog_resume",
                        f"Resumed keygen: backlog {backlog} ≤ {CHECKER_BACKLOG_PAUSE_THRESHOLD}",
                    )
            try:
                process_pending_vanity_outputs_once(logger)
            except Exception as e:
                logger.warning(f"⚠️ btc_only processing tick encountered an error but will continue: {e}")
            time.sleep(2)
    except KeyboardInterrupt:
        log_message("Shutting down BTC-only mode", "INFO")
    finally:
        shutdown_event.set()
        keygen_shutdown.set()
        try:
            keygen_proc.join(timeout=5)
        except Exception:
            pass
        try:
            metrics_proc.terminate()
        except Exception:
            pass
        stop_listener()

def run_allinkeys(args):
    # Enable bech32 modes when explicitly requested via CLI.  Settings
    # default to legacy P2PKH only.
    if getattr(args, "enable_bc1", False):
        settings.ENABLE_P2WPKH = True
        settings.ENABLE_TAPROOT = True
    if getattr(args, "only", None) == "btc":
        run_btc_only(args)
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    start_listener()
    os.environ.setdefault("PYOPENCL_COMPILER_OUTPUT", "1")
    display_logo()

    assign_gpu_roles()
    test_csv = os.path.join(DOWNLOAD_DIR, "test_alerts.csv")
    if not os.path.exists(test_csv):
        generate_test_csv()

    # Initialize shared metrics manager and create events from it so they can be
    # passed safely to worker processes spawned via ``spawn``.
    shared_metrics = init_dashboard_manager()

    # ``multiprocessing.Manager().Event`` objects can trigger ``KeyError`` when
    # forwarded through a ``ProcessPoolExecutor``.  Using plain
    # ``multiprocessing.Event`` avoids proxy lookups that occasionally fail when
    # worker processes start up or exit.  Events are created once here and then
    # shared with child processes.
    shutdown_event = multiprocessing.Event()
    shutdown_events = {
        'keygen': multiprocessing.Event(),
        'altcoin': multiprocessing.Event(),
        'csv_check': multiprocessing.Event(),
        'csv_recheck': multiprocessing.Event(),
    }
    pause_events = {
        'keygen': multiprocessing.Event(),
        'altcoin': multiprocessing.Event(),
        'csv_check': multiprocessing.Event(),
        'csv_recheck': multiprocessing.Event(),
    }
    from core.dashboard import register_control_events, get_pause_event
    register_control_events(shutdown_event, None)  # global events
    for name, ev in pause_events.items():
        register_control_events(shutdown_events.get(name), ev, module=name)
        pause_events[name] = get_pause_event(name)
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

    from core.logger import log_queue
    processes, named_processes = run_all_processes(args, shutdown_events, shared_metrics, pause_events, log_queue)

    def monitor():
        from core.dashboard import get_current_metrics
        while not shutdown_event.is_set():
            status = get_current_metrics().get("status", {})
            update_dashboard_stat("thread_health_flags", status)
            time.sleep(2)

    threading.Thread(target=monitor, daemon=True).start()

    try:
        if ENABLE_DASHBOARD and not args.no_dashboard:
            start_dashboard()
        else:
            while not shutdown_event.is_set():
                time.sleep(10)
    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C received. Shutting down gracefully...", flush=True)
    finally:
        shutdown_event.set()
        for ev in shutdown_events.values():
            ev.set()
        for p in processes:
            try:
                p.join(timeout=5)
            except Exception:
                pass
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join()
        try:
            log_queue.put_nowait(None)
        except Exception:
            pass
        try:
            stop_listener()
        except Exception:
            pass


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # already set

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
    parser.add_argument("--enable-bc1", action="store_true", help="Enable bc1/bech32 address generation")
    parser.add_argument("-only", choices=["btc"], help="Restrict to a single coin flow.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-all", action="store_true", help="Use 'all BTC addresses ever used' range mode")
    mode.add_argument("-funded", action="store_true", help="Use daily funded BTC list")
    args = parser.parse_args()

    run_allinkeys(args)
