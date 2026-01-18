import os
import time
import threading
import multiprocessing
from datetime import datetime, timedelta
from multiprocessing import Process

import psutil
from dashboard.metrics_window import CPUPercent

from core.logger import get_logger, log_message, log_with_context, start_listener, stop_listener
from utils.thread_guard import can_spawn_thread

import config.settings as settings
from config.settings import (
    ENABLE_CHECKPOINT_RESTORE,
    CHECKPOINT_INTERVAL_SECONDS,
    LOGO_ART,
    ENABLE_DAY_ONE_CHECK,
    ENABLE_UNIQUE_RECHECK,
    ENABLE_DASHBOARD,
    ENABLE_KEYGEN,
    ENABLE_ALERTS,
    ENABLE_BACKLOG_CONVERSION,
)
from config.directories import LOG_DIR, CSV_DIR, DOWNLOAD_DIR, VANITY_OUTPUT_DIR
from config.telemetry import TELEMETRY_SERVICE_HOST, TELEMETRY_SERVICE_PORT

from core.checkpoint import load_keygen_checkpoint, save_keygen_checkpoint
from core.downloader import download_and_compare_address_lists, generate_test_csv
from core.csv_checker import check_csvs_day_one, check_csvs
from core.alerts import trigger_startup_alerts, alert_match
from core.dashboard import (
    update_dashboard_stat,
    init_shared_metrics,
    init_dashboard_manager,
    get_current_metrics,
    get_metric,
    set_metric,
)
from core.gpu_selector import (
    assign_gpu_roles,
    get_vanitysearch_gpu_ids,
    get_altcoin_gpu_ids,
    get_gpu_assignments,
)
from core.altcoin_derive import start_altcoin_conversion_process
from core.telemetry import start_telemetry, start_embedded_telemetry_service
from utils.file_utils import start_daily_cleanup, cleanup_old_files

logger = get_logger(__name__)

try:
    import GPUtil
except ImportError:
    GPUtil = None

try:
    import pyopencl as cl
except ImportError:
    cl = None

# Track disk free space to estimate fill ETA
_last_disk_check = (time.time(), psutil.disk_usage("/").free)
# Track backlog processing for ETA calculations
_backlog_total_time = 0.0
_backlog_processed = 0
_backlog_last_ts = time.time()
_last_csv_created = 0


def display_logo():
    print(LOGO_ART, flush=True)
    print("\nIf you like this software...donate!\n", flush=True)
    print("BTC: 18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y", flush=True)
    print("LTC: LNmgLkonXtecopmGauqsDFvci4XQTZAWmg", flush=True)
    print("DOGE: DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5", flush=True)
    print(
        "XMR: 43DUJ1MA7Mv1n4BTRHemEbDmvYzMysVt2djHnjGzrHZBb4WgMDtQHWh51ZfbcVwHP8We6pML4f1Q7SNEtveYCk4HDdb14ik",
        flush=True,
    )
    print("ETH: 0xCb8B2937D60c47438562A2E53d08B85865B57741", flush=True)
    print("PEP: PbCiPTNrYaCgv1aqNCds5n7Q73znGrTkgp\n", flush=True)


def save_checkpoint_loop():
    while True:
        try:
            from core.keygen import keygen_progress

            save_keygen_checkpoint(keygen_progress())
            logger.debug("💾 Checkpoint saved.")
        except Exception as e:
            logger.error(f"❌ Error in checkpoint save loop: {e}")
        time.sleep(CHECKPOINT_INTERVAL_SECONDS)


def metrics_updater(shared_metrics=None, shutdown_event=None):
    from core.worker_bootstrap import ensure_metrics_ready

    try:
        ensure_metrics_ready(shared_metrics)
        print("[debug] Shared metrics initialized for", __name__, flush=True)
    except Exception as e:
        print(f"[error] ensure_metrics_ready failed in {__name__}: {e}", flush=True)
    last_kps = 0.0
    stop_event = shutdown_event or threading.Event()
    cpu_sampler = CPUPercent()

    def update():
        nonlocal last_kps
        global _last_disk_check, _backlog_total_time, _backlog_processed, _backlog_last_ts, _last_csv_created
        from core.dashboard import reset_daily_metrics_if_needed

        reset_daily_metrics_if_needed()
        from core.keygen import keygen_progress

        now = time.time()
        disk_usage = psutil.disk_usage("/")
        disk_free = disk_usage.free
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
        cpu_percent = cpu_sampler.sample()
        disk_free_percent = (
            (disk_free / disk_usage.total) * 100 if disk_usage.total else 0
        )
        gpu_load_percent = None
        gpu_name = None
        stats = {
            "cpu_usage": f"{cpu_percent:.1f}%",
            "cpu_percent": round(cpu_percent, 1),
            "ram_usage": f"{vm.used / (1024 ** 3):.1f} GB / {vm.total / (1024 ** 3):.1f} GB ({ram_percent}%)",
            "ram_percent": round(ram_percent, 1),
            "disk_free_gb": round(disk_free / (1024**3), 2),
            "disk_free_percent": round(disk_free_percent, 1),
            "disk_fill_eta": disk_eta,
            "time_to_disk_full": disk_eta,
            "gpu_stats": {},
            "gpu_load_percent": "N/A",
            "gpu_name": "N/A",
            "gpu_assignments": get_gpu_assignments(),
        }
        vs_ids = set(get_vanitysearch_gpu_ids())
        ad_ids = set(get_altcoin_gpu_ids())

        if GPUtil:
            try:
                gpus = GPUtil.getGPUs()
                for gpu in gpus:
                    try:
                        load_percent = gpu.load * 100
                        usage = f"{load_percent:.0f}%"
                        vram = f"{gpu.memoryUsed/1024:.1f}GB / {gpu.memoryTotal/1024:.1f}GB"
                    except Exception:
                        usage = "N/A"
                        vram = "Unavailable"
                    name = gpu.name
                    if gpu_load_percent is None or (
                        isinstance(load_percent, (int, float))
                        and load_percent > gpu_load_percent
                    ):
                        gpu_load_percent = load_percent
                        gpu_name = gpu.name
                    if gpu.id in vs_ids:
                        name += " (VS)"
                    if gpu.id in ad_ids:
                        name += " (AD)"
                    if usage in ["N/A", None]:
                        usage = (
                            "Active (No Stats)"
                            if gpu.id in ad_ids | vs_ids
                            else "N/A"
                        )
                    stats["gpu_stats"][f"GPU{gpu.id}"] = {
                        "name": name,
                        "usage": usage,
                        "vram": vram,
                        "temp": (
                            f"{gpu.temperature}°C"
                            if hasattr(gpu, "temperature")
                            else "N/A"
                        ),
                    }
            except Exception as e:
                logger.warning(f"⚠️ GPU read failed: {e}")

        next_id = len(stats["gpu_stats"])
        if cl:
            try:
                for platform in cl.get_platforms():
                    for device in platform.get_devices():
                        already = any(
                            info.get("name", "").startswith(device.name)
                            for info in stats["gpu_stats"].values()
                        )
                        if already:
                            continue
                        name = device.name
                        roles = []
                        if next_id in vs_ids:
                            roles.append("VS")
                        if next_id in ad_ids:
                            roles.append("AD")
                        if roles:
                            name += " (" + "/".join(roles) + ")"
                        usage = "Active (No Stats)" if roles else "N/A"
                        stats["gpu_stats"][f"GPU{next_id}"] = {
                            "name": name,
                            "usage": usage,
                            "vram": "Unavailable",
                            "temp": "N/A",
                        }
                        if gpu_name is None:
                            gpu_name = device.name
                        next_id += 1
            except Exception as e:
                logger.warning(f"⚠️ OpenCL GPU read failed: {e}")

        if gpu_load_percent is not None:
            stats["gpu_load_percent"] = round(gpu_load_percent, 1)
        if gpu_name:
            stats["gpu_name"] = gpu_name

        # ----- Backlog ETA Calculation -----
        metrics_snapshot = get_current_metrics()
        queue_count = metrics_snapshot.get("backlog_files_queued", 0)
        created_today = metrics_snapshot.get("csv_created_today", 0)
        if created_today > _last_csv_created:
            _backlog_total_time += now - _backlog_last_ts
            _backlog_processed += created_today - _last_csv_created
            _backlog_last_ts = now
            _last_csv_created = created_today

        if _backlog_processed > 0:
            avg_time = _backlog_total_time / _backlog_processed
            stats["backlog_avg_time"] = f"{avg_time:.2f}s"
            if queue_count > 0:
                eta_sec = avg_time * queue_count
                stats["backlog_eta"] = str(timedelta(seconds=int(eta_sec)))
            else:
                stats["backlog_eta"] = "N/A"
        else:
            stats["backlog_eta"] = "N/A"

        prog = keygen_progress()
        curr_lifetime = get_metric("keys_generated_lifetime", 0)
        current_kps = get_metric("keys_per_sec", 0)
        if current_kps > 0:
            last_kps = current_kps
        else:
            status = get_metric("status", {}).get("keygen", "Stopped")
            if status == "Running":
                current_kps = last_kps
            else:
                last_kps = 0.0
        stats["keys_generated_lifetime"] = curr_lifetime
        stats["keys_per_sec"] = round(current_kps, 2)
        stats["uptime"] = prog["elapsed_time"]
        stats["last_updated"] = datetime.utcnow().strftime("%H:%M:%S")
        try:
            from config.settings import BATCH_SIZE

            stats["vanity_progress_percent"] = round(
                (prog.get("index_within_batch", 0) / float(BATCH_SIZE)) * 100,
                2,
            )
        except Exception:
            stats["vanity_progress_percent"] = 0
        update_dashboard_stat(stats)
        logger.debug(f"📊 Metrics updated: {stats}")

    failure_delay_seconds = 1
    logger.debug("📊 Metrics updater loop starting.")
    # Use a long-lived loop with stop_event.wait(...) to avoid Timer recursion and thread buildup.
    while not stop_event.is_set():
        try:
            update()
        except Exception as exc:
            logger.error(
                "metrics_updater failed",
                extra={"exception": str(exc)},
            )
            if stop_event.wait(failure_delay_seconds):
                break
        if stop_event.wait(settings.METRICS_POLL_INTERVAL_SECONDS):
            break
    logger.debug("📊 Metrics updater loop stopped.")


def should_skip_download_today(download_dir):
    today_str = datetime.now().strftime("%Y-%m-%d")
    return any(today_str in f for f in os.listdir(download_dir) if f.endswith(".txt"))


def run_all_processes(args, shutdown_events, shared_metrics, pause_events, log_q):
    from core.keygen import start_keygen_loop
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
        shared_metrics, shutdown_events.get("keygen")
    )
    processes.append(gpu_sched)
    named_processes.append(("gpu_scheduler", gpu_sched))

    if ENABLE_CHECKPOINT_RESTORE:
        load_keygen_checkpoint()
        logger.info("🧠 Checkpoint restore enabled.")

    if not args.skip_downloads:
        if should_skip_download_today(DOWNLOAD_DIR):
            logger.info("🚩 Skipping address downloads — already downloaded today.")
        else:
            logger.info("🌐 Downloading address lists...")
            download_and_compare_address_lists()
    else:
        # Ensure test CSV exists even when downloads are skipped
        generate_test_csv()

    backlog_files = []
    try:
        backlog_files = [f for f in os.listdir(VANITY_OUTPUT_DIR) if f.endswith(".txt")]
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
        logger.info("[Startup] Detected backlog of 100+ files; delaying VanitySearch.")
        set_metric("status.keygen", "Stopped")
        vanity_gpu_flag.value = 0
        altcoin_gpu_flag.value = 1
        assignment_flag.value = 1
        set_metric("vanity_gpu_on", False)
        set_metric("altcoin_gpu_on", True)
        set_metric("gpu_assignment", "altcoin")
    elif ENABLE_KEYGEN and not args.headless:
        try:
            p = Process(
                target=start_keygen_loop,
                args=(
                    shared_metrics,
                    shutdown_events.get("keygen"),
                    pause_events.get("keygen"),
                    vanity_gpu_flag,
                ),
            )
            p.daemon = True
            p.start()
            logger.info("[Started] Keygen subprocess")
            processes.append(p)
            named_processes.append(("keygen", p))
        except Exception as e:
            logger.error(f"❌ Failed to launch keygen: {e}")

    if ENABLE_DAY_ONE_CHECK:
        try:
            p = Process(
                target=check_csvs_day_one,
                args=(
                    shared_metrics,
                    shutdown_events.get("csv_check"),
                    pause_events.get("csv_check"),
                    False,
                    None,
                    log_q,
                ),
            )
            p.daemon = True
            p.start()
            logger.info("[Started] Day One CSV checker")
            processes.append(p)
            named_processes.append(("csv_check", p))
        except Exception as e:
            logger.error(f"❌ Failed to start day-one checker: {e}")

    if ENABLE_UNIQUE_RECHECK:
        try:
            p = Process(
                target=check_csvs,
                args=(
                    shared_metrics,
                    shutdown_events.get("csv_recheck"),
                    pause_events.get("csv_recheck"),
                    False,
                    None,
                    log_q,
                ),
            )
            p.daemon = True
            p.start()
            logger.info("[Started] Unique recheck")
            processes.append(p)
            named_processes.append(("csv_recheck", p))
        except Exception as e:
            logger.error(f"❌ Failed to start recheck: {e}")

    if ENABLE_BACKLOG_CONVERSION and not args.skip_backlog:
        try:
            p = start_altcoin_conversion_process(
                shutdown_events.get("altcoin"),
                shared_metrics,
                pause_events.get("altcoin"),
                log_q,
                altcoin_gpu_flag,
            )
            logger.info("[Started] Altcoin derive subprocess")
            processes.append(p)
            named_processes.append(("altcoin", p))
        except Exception as e:
            logger.error(f"❌ Failed to start altcoin convert: {e}")

    if ENABLE_ALERTS:
        try:
            p = Process(target=trigger_startup_alerts, args=(shared_metrics,))
            p.daemon = True
            p.start()
            logger.info("[Started] Startup alerts")
            processes.append(p)
            named_processes.append(("alerts", p))
        except Exception as e:
            logger.error(f"❌ Failed to trigger startup alerts: {e}")

    if CHECKPOINT_INTERVAL_SECONDS:
        try:
            p = Process(target=save_checkpoint_loop)
            p.daemon = True
            p.start()
            logger.info("[Started] Checkpoint saver")
            processes.append(p)
            named_processes.append(("checkpoint", p))
        except Exception as e:
            logger.error(f"❌ Failed to start checkpoint saver: {e}")

    try:
        p = Process(
            target=metrics_updater,
            args=(shared_metrics, shutdown_events.get("metrics")),
        )
        p.daemon = True
        p.start()
        logger.info("[Started] Metrics updater")
        processes.append(p)
        named_processes.append(("metrics", p))
    except Exception as e:
        logger.error(f"❌ Failed to launch metrics updater: {e}")

    return processes, named_processes


def run_allinkeys(args, shared_metrics=None):
    # Enable bech32 modes when explicitly requested via CLI.  Settings
    # default to legacy P2PKH only.
    if getattr(args, "enable_bc1", False):
        settings.ENABLE_P2WPKH = True
        settings.ENABLE_TAPROOT = True
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    start_listener()
    if getattr(args, "purge", False):
        removed = cleanup_old_files()
        log_message(
            f"\U0001f9f9 Purged {removed} file(s) older than {settings.RETENTION_DAYS} days from downloads"
        )
        return 0
    os.environ.setdefault("PYOPENCL_COMPILER_OUTPUT", "1")
    display_logo()

    assign_gpu_roles(getattr(args, "gpu_index", None))
    test_csv = os.path.join(DOWNLOAD_DIR, "test_alerts.csv")
    if not os.path.exists(test_csv):
        generate_test_csv()
    start_daily_cleanup()

    # Initialize shared metrics manager and create events from it so they can be
    # passed safely to worker processes spawned via ``spawn``.
    if shared_metrics is None:
        shared_metrics = init_dashboard_manager()

    # ``multiprocessing.Manager().Event`` objects can trigger ``KeyError`` when
    # forwarded through a ``ProcessPoolExecutor``.  Using plain
    # ``multiprocessing.Event`` avoids proxy lookups that occasionally fail when
    # worker processes start up or exit.  Events are created once here and then
    # shared with child processes.
    shutdown_event = multiprocessing.Event()
    if settings.SEED_TELEMETRY_ENABLED and not getattr(args, "no_telemetry", False):
        # Start background client flusher
        start_telemetry(shutdown_event)
        # Optionally run the embedded central service on this node
        try:
            svc_proc = start_embedded_telemetry_service()
            if svc_proc is not None:
                log_with_context(
                    logger,
                    "INFO",
                    "[Started] Embedded telemetry service",
                    endpoint=f"http://{TELEMETRY_SERVICE_HOST}:{TELEMETRY_SERVICE_PORT}",
                )
        except Exception as e:
            log_with_context(
                logger,
                "WARNING",
                f"Failed to start embedded telemetry service: {e}",
                endpoint=f"http://{TELEMETRY_SERVICE_HOST}:{TELEMETRY_SERVICE_PORT}",
            )
    shutdown_events = {
        "keygen": multiprocessing.Event(),
        "altcoin": multiprocessing.Event(),
        "csv_check": multiprocessing.Event(),
        "csv_recheck": multiprocessing.Event(),
        "metrics": multiprocessing.Event(),
    }
    pause_events = {
        "keygen": multiprocessing.Event(),
        "altcoin": multiprocessing.Event(),
        "csv_check": multiprocessing.Event(),
        "csv_recheck": multiprocessing.Event(),
    }
    from core.dashboard import register_control_events, get_pause_event

    register_control_events(shutdown_event, None)  # global events
    for name, ev in pause_events.items():
        register_control_events(shutdown_events.get(name), ev, module=name)
        pause_events[name] = get_pause_event(name)
    register_control_events(shutdown_events.get("metrics"), None, module="metrics")
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
            "test_mode": True,
        }
        logger.info("🧺 Running simulated match alert...")
        alert_match(test_data, test_mode=True)

    from core.logger import log_queue

    processes, named_processes = run_all_processes(
        args, shutdown_events, shared_metrics, pause_events, log_queue
    )
    # If embedded telemetry service was started, add it for graceful shutdown
    try:
        if "svc_proc" in locals() and svc_proc is not None:
            processes.append(svc_proc)
            named_processes.append(("telemetry_service", svc_proc))
    except Exception:
        pass

    def monitor():
        from core.dashboard import get_current_metrics

        while not shutdown_event.is_set():
            status = get_current_metrics().get("status", {})
            update_dashboard_stat("thread_health_flags", status)
            time.sleep(2)

    if can_spawn_thread("dashboard_monitor"):
        threading.Thread(target=monitor, daemon=True).start()
    else:
        logger.warning("[ThreadGuard] Dashboard monitor thread skipped")

    try:
        if (
            ENABLE_DASHBOARD
            and not args.no_dashboard
            and not getattr(args, "headless", False)
        ):
            from ui.dashboard_gui import start_dashboard

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
    return 0


def start(shared_metrics, args):
    return run_allinkeys(args, shared_metrics=shared_metrics)
