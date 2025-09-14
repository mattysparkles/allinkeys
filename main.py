# main.py

import os
import io
import time
import sys
import argparse
import multiprocessing
import threading
import subprocess
from datetime import datetime, timedelta
from multiprocessing import Process
import psutil
from dashboard.metrics_window import CPUPercent
from core.logger import get_logger

# Wrap stdout once with UTF-8 encoding if not already wrapped
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
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
_last_disk_check = (time.time(), psutil.disk_usage("/").free)
# Track backlog processing for ETA calculations
_backlog_total_time = 0.0
_backlog_processed = 0
_backlog_last_ts = time.time()
_last_csv_created = 0

logger = get_logger(__name__)

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
    LOG_DIR,
    CSV_DIR,
    DOWNLOAD_DIR,
    VANITY_OUTPUT_DIR,
    find_vanitysearch_binary,
)

from core.logger import log_message, start_listener, stop_listener
from core.checkpoint import load_keygen_checkpoint, save_keygen_checkpoint
from core.downloader import download_and_compare_address_lists, generate_test_csv
from core.csv_checker import check_csvs_day_one, check_csvs
from core.btc_only_checker import btc_only_checker_loop
from core.alerts import trigger_startup_alerts, alert_match
from core.dashboard import (
    update_dashboard_stat,
    init_shared_metrics,
    init_dashboard_manager,
    get_current_metrics,
    get_metric,
    set_metric,
)
from core.gpu_selector import assign_gpu_roles
from core.altcoin_derive import start_altcoin_conversion_process  # <-- updated import
from core.telemetry import start_telemetry
from utils.file_utils import start_daily_cleanup, cleanup_old_files


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


from core.gpu_selector import (
    get_vanitysearch_gpu_ids,
    get_altcoin_gpu_ids,
    get_gpu_assignments,
)


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
        if stop_event.is_set():
            return
        global _last_disk_check, _backlog_total_time, _backlog_processed, _backlog_last_ts, _last_csv_created
        try:
            from core.dashboard import reset_daily_metrics_if_needed

            reset_daily_metrics_if_needed()
            from core.keygen import keygen_progress

            now = time.time()
            disk_free = psutil.disk_usage("/").free
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
                "cpu_usage": f"{cpu_sampler.sample():.1f}%",
                "ram_usage": f"{vm.used / (1024 ** 3):.1f} GB / {vm.total / (1024 ** 3):.1f} GB ({ram_percent}%)",
                "disk_free_gb": round(disk_free / (1024**3), 2),
                "disk_fill_eta": disk_eta,
                "gpu_stats": {},
                "gpu_assignments": get_gpu_assignments(),
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
                            next_id += 1
                except Exception as e:
                    logger.warning(f"⚠️ OpenCL GPU read failed: {e}")

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
        except Exception as e:
            log_message(f"❌ Error in metrics updater: {e}", "ERROR")
        if not stop_event.is_set():
            threading.Timer(settings.METRICS_POLL_INTERVAL_SECONDS, update).start()

    update()
    stop_event.wait()


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


def resolve_btc_compression(args):
    """Determine whether BTC addresses should be compressed."""
    if getattr(args, "puzzle", None) is not None:
        return True
    if getattr(args, "compressed", False):
        return True
    if getattr(args, "uncompressed", False):
        return False
    return getattr(args, "addr_format", "compressed") == "compressed"


COIN_OPTIONS = ["btc", "bch", "ltc", "doge", "dash", "rvn", "pep", "eth"]


def _parse_only(value: str) -> list[str]:
    coins = [c.strip().lower() for c in value.split(",") if c.strip()]
    for c in coins:
        if c not in COIN_OPTIONS:
            raise argparse.ArgumentTypeError(f"Unknown coin option: {c}")
    return coins


def handle_deprecated_flags(args):
    if getattr(args, "only_legacy", None) and not getattr(args, "only", None):
        args.only = args.only_legacy
        print("Warning: '-only' is deprecated; use '--only' instead.", file=sys.stderr)
    if getattr(args, "all_legacy", False) and not getattr(args, "all", False):
        args.all = True
        print("Warning: '-all' is deprecated; use '--all' instead.", file=sys.stderr)
    if getattr(args, "funded_legacy", False) and not getattr(args, "funded", False):
        args.funded = True
        print(
            "Warning: '-funded' is deprecated; use '--funded' instead.", file=sys.stderr
        )


def handle_puzzle_mode(args):
    if getattr(args, "puzzle", None) is None:
        return
    from utils.puzzle import get_puzzle_info

    info = get_puzzle_info(args.puzzle)
    settings.PUZZLE_MODE = True
    settings.PUZZLE_NUMBER = args.puzzle
    settings.PUZZLE_START = info["start"]
    settings.PUZZLE_END = info["end"]
    settings.PUZZLE_CHUNK_INDEX = getattr(args, "chunk", None)
    if getattr(args, "every", False):
        settings.VANITY_PATTERN = "1**"
    else:
        settings.VANITY_PATTERN = info["address"]
    args.compressed = True


def run_only_mode(args):
    """Dispatch flows when ``--only`` is provided."""
    coins = getattr(args, "only", None)
    if not coins:
        return None

    if coins == ["btc"]:
        compressed = resolve_btc_compression(args)
        os.makedirs(LOG_DIR, exist_ok=True)
        start_listener()

        # Even in BTC-only mode we want to give the user a chance to pick
        # which GPU should handle VanitySearch.  Previously this mode skipped
        # the interactive assignment entirely which caused confusion when the
        # key generator silently defaulted to the first device.  Prompting here
        # mirrors the behaviour of the full application and ensures the
        # ``gpu_assignments.json`` file is always created.
        from core.gpu_selector import assign_gpu_roles

        assign_gpu_roles(getattr(args, "gpu_index", None))

        shared_metrics = init_dashboard_manager()
        shutdown_keygen = multiprocessing.Event()
        pause_keygen = multiprocessing.Event()
        shutdown_btc = multiprocessing.Event()
        pause_btc = multiprocessing.Event()
        shutdown_metrics = multiprocessing.Event()
        vanity_gpu_flag = multiprocessing.Value("i", 1)

        processes = []
        from core.logger import log_queue

        # Background metrics collector so the GUI has real-time stats
        p = Process(target=metrics_updater, args=(shared_metrics, shutdown_metrics))
        p.daemon = True
        p.start()
        processes.append(p)

        # BTC-only vanity output checker
        p = Process(
            target=btc_only_checker_loop,
            args=(
                shared_metrics,
                shutdown_btc,
                pause_btc,
                log_queue,
                args.all,
                args.skip_downloads,
            ),
        )
        p.daemon = True
        p.start()
        processes.append(p)

        # Launch the dashboard UI unless explicitly disabled.  Because the
        # BTC-only flow blocks while running the key generator, the GUI is
        # started on a background thread so the main thread can continue with
        # key generation.
        if (
            ENABLE_DASHBOARD
            and not getattr(args, "no_dashboard", False)
            and not getattr(args, "headless", False)
        ):
            from ui.dashboard_gui import start_dashboard

            threading.Thread(target=start_dashboard, daemon=True).start()

        from core.keygen import run_btc_only  # call into keygen module

        try:
            return run_btc_only(
                compressed=compressed,
                shared_metrics=shared_metrics,
                shutdown_event=shutdown_keygen,
                pause_event=pause_keygen,
                gpu_flag=vanity_gpu_flag,
            )
        finally:
            shutdown_keygen.set()
            shutdown_btc.set()
            shutdown_metrics.set()
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
                    proc.join()
            stop_listener()
        return 0

    print(
        f"Warning: altcoin-only mode not fully implemented for: {', '.join(coins)}",
        file=sys.stderr,
    )
    return 1


def run_allinkeys(args):
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
        return
    os.environ.setdefault("PYOPENCL_COMPILER_OUTPUT", "1")
    display_logo()

    assign_gpu_roles(getattr(args, "gpu_index", None))
    test_csv = os.path.join(DOWNLOAD_DIR, "test_alerts.csv")
    if not os.path.exists(test_csv):
        generate_test_csv()
    start_daily_cleanup()

    # Initialize shared metrics manager and create events from it so they can be
    # passed safely to worker processes spawned via ``spawn``.
    shared_metrics = init_dashboard_manager()

    # ``multiprocessing.Manager().Event`` objects can trigger ``KeyError`` when
    # forwarded through a ``ProcessPoolExecutor``.  Using plain
    # ``multiprocessing.Event`` avoids proxy lookups that occasionally fail when
    # worker processes start up or exit.  Events are created once here and then
    # shared with child processes.
    shutdown_event = multiprocessing.Event()
    if settings.SEED_TELEMETRY_ENABLED and not getattr(args, "no_telemetry", False):
        start_telemetry(shutdown_event)
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

    def monitor():
        from core.dashboard import get_current_metrics

        while not shutdown_event.is_set():
            status = get_current_metrics().get("status", {})
            update_dashboard_stat("thread_health_flags", status)
            time.sleep(2)

    threading.Thread(target=monitor, daemon=True).start()

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


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level :class:`argparse.ArgumentParser`.

    The parser construction is factored out so tests can easily verify CLI
    behaviour without invoking the full application.
    """

    parser = argparse.ArgumentParser(description="AllInKeys Modular Runner")
    parser.add_argument(
        "--skip-backlog", action="store_true", help="Skip backlog conversion on startup"
    )
    parser.add_argument(
        "--no-dashboard", action="store_true", help="Don't launch GUI dashboard"
    )
    parser.add_argument(
        "--dashboard-password", help="Password required to access the dashboard"
    )
    parser.add_argument(
        "--skip-downloads", action="store_true", help="Skip downloading balance files"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run without any GUI or visuals"
    )
    parser.add_argument(
        "--no-telemetry", action="store_true", help="Disable telemetry reporting"
    )
    parser.add_argument(
        "--match-test", action="store_true", help="Trigger fake match alert on startup"
    )
    parser.add_argument(
        "--purge",
        nargs="?",
        const="30",
        metavar="DAYS",
        help="Remove files older than DAYS (default 30) in output/vanity_output/ and output/csv/",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview purge actions without deleting"
    )
    parser.add_argument(
        "--enable-bc1", action="store_true", help="Enable bc1/bech32 address generation"
    )
    parser.add_argument(
        "--only",
        type=_parse_only,
        dest="only",
        help="Restrict to coin flow(s). Comma-separated list.",
    )
    parser.add_argument(
        "-only", type=_parse_only, dest="only_legacy", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--puzzle", type=int, help="Run BTC puzzle mode for given puzzle number"
    )
    parser.add_argument(
        "--chunk", type=int, help="Puzzle mode: claim specific chunk index"
    )
    parser.add_argument(
        "--gpu-index", type=int, help="Force use of a specific GPU device index"
    )
    puzzle_group = parser.add_mutually_exclusive_group()
    puzzle_group.add_argument(
        "--every", action="store_true", help="Puzzle mode: keep generic '1**' prefix"
    )
    puzzle_group.add_argument(
        "--target",
        action="store_true",
        help="Puzzle mode: target puzzle address (default)",
    )
    parser.add_argument(
        "--addr-format",
        choices=["compressed", "uncompressed"],
        default="compressed",
        help="BTC-only address format (default: compressed)",
    )
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--compressed", action="store_true", help="BTC-only: force compressed addresses"
    )
    fmt_group.add_argument(
        "--uncompressed",
        action="store_true",
        help="BTC-only: force uncompressed addresses",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="Use 'all BTC addresses ever used' range mode",
    )
    mode.add_argument("--funded", action="store_true", help="Use daily funded BTC list")
    mode.add_argument(
        "-all", dest="all_legacy", action="store_true", help=argparse.SUPPRESS
    )
    mode.add_argument(
        "-funded", dest="funded_legacy", action="store_true", help=argparse.SUPPRESS
    )

    # ------------------------------------------------------------------
    # Mnemonic mode flags
    # ------------------------------------------------------------------
    mnemonic_group = parser.add_argument_group(
        "Mnemonic Mode",
        "Generate BIP-39 mnemonic phrases and derive addresses without running VanitySearch",
    )
    mnemonic_group.add_argument(
        "--mnemonic",
        action="store_true",
        help="Enable mnemonic generation mode (skip VanitySearch)",
    )
    for i in range(3, 26):
        mnemonic_group.add_argument(
            f"--{i}words",
            dest="num_words",
            action="store_const",
            const=i,
            help=f"Generate {i}-word mnemonic phrase",
        )

    mnemonic_group.add_argument(
        "--bip39",
        action="store_true",
        help="Use the default BIP39 English wordlist",
    )
    mnemonic_group.add_argument(
        "--custom-words-file",
        help="Path to a custom word list for mnemonic generation",
    )
    lang_group = mnemonic_group.add_mutually_exclusive_group()
    lang_group.add_argument(
        "--spanish",
        action="store_true",
        help="Use BIP39 Spanish wordlist",
    )
    lang_group.add_argument(
        "--french",
        action="store_true",
        help="Use BIP39 French wordlist",
    )
    lang_group.add_argument(
        "--italian",
        action="store_true",
        help="Use BIP39 Italian wordlist",
    )
    lang_group.add_argument(
        "--japanese",
        action="store_true",
        help="Use BIP39 Japanese wordlist",
    )
    lang_group.add_argument(
        "--korean",
        action="store_true",
        help="Use BIP39 Korean wordlist",
    )
    lang_group.add_argument(
        "--czech",
        action="store_true",
        help="Use BIP39 Czech wordlist",
    )
    lang_group.add_argument(
        "--portuguese",
        action="store_true",
        help="Use BIP39 Portuguese wordlist",
    )
    lang_group.add_argument(
        "--chinese",
        action="store_true",
        help="Use BIP39 Traditional Chinese wordlist",
    )
    lang_group.add_argument(
        "--chinese-simple",
        action="store_true",
        help="Use BIP39 Simplified Chinese wordlist",
    )
    mnemonic_group.add_argument(
        "--coins",
        type=_parse_only,
        help="Comma-separated list of coins to derive (e.g., btc,eth)",
    )
    mnemonic_group.add_argument(
        "--allcoins",
        action="store_true",
        help="Derive all supported coins",
    )
    mnemonic_group.add_argument(
        "--atomic",
        action="store_true",
        help="Use Atomic wallet derivation paths",
    )
    mnemonic_group.add_argument(
        "--coinomi",
        action="store_true",
        help="Use Coinomi wallet paths",
    )
    mnemonic_group.add_argument(
        "--ledger",
        action="store_true",
        help="Use Ledger wallet paths",
    )
    mnemonic_group.add_argument(
        "--trust",
        action="store_true",
        help="Use Trust wallet paths",
    )
    mnemonic_group.add_argument(
        "--trezor",
        action="store_true",
        help="Use Trezor wallet paths",
    )
    mnemonic_group.add_argument(
        "--path",
        dest="global_path",
        help="Custom derivation path for all coins",
    )
    for _coin in ["btc", "bch", "ltc", "eth", "dash", "doge", "pep", "rvn"]:
        mnemonic_group.add_argument(
            f"--{_coin}-path",
            dest=f"{_coin}_path",
            help=f"Custom derivation path for {_coin.upper()}",
        )
    mnemonic_group.add_argument(
        "--gpu",
        action="store_true",
        help="Enable OpenCL acceleration if available",
    )
    mnemonic_group.add_argument(
        "--gpu-id",
        type=int,
        help="Select specific GPU device for mnemonic mode",
    )
    mnemonic_group.add_argument(
        "--no-gpu",
        action="store_true",
        help="Force the CPU implementation",
    )
    mnemonic_group.add_argument(
        "--rng-seed",
        type=int,
        help="Deterministic RNG seed for mnemonics",
    )
    mnemonic_group.add_argument(
        "--passphrase",
        default="",
        help="Optional BIP39 passphrase",
    )
    mnemonic_group.add_argument(
        "--rate-limit",
        type=int,
        help="Throttle derivations per second",
    )
    mnemonic_group.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Mnemonic mode batch size",
    )
    mnemonic_group.add_argument(
        "--threads",
        type=int,
        default=1,
        help="CPU threads for mnemonic mode",
    )
    mnemonic_group.add_argument(
        "--progress-interval",
        type=int,
        default=10,
        help="Progress update interval in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point used by ``__main__`` and tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_telemetry", False):
        settings.SEED_TELEMETRY_ENABLED = False
    # Handle retention purge early and exit
    if getattr(args, "purge", None) is not None:
        try:
            days = int(args.purge)
        except Exception:
            print("Invalid DAYS for --purge; must be an integer.", flush=True)
            return 2
        from core.utils.retention import purge_older_than

        _, messages = purge_older_than(days, dry_run=getattr(args, "dry_run", False))
        for m in messages:
            print(m, flush=True)
        return 0
    if getattr(args, "dashboard_password", None):
        from utils.auth import hash_password

        settings.DASHBOARD_PASSWORD_HASH = hash_password(args.dashboard_password)
    handle_deprecated_flags(args)
    if getattr(args, "mnemonic", False):
        # Lazy import to keep startup fast for other modes
        from keygen.mnemonic_mode import run_mnemonic_mode

        run_mnemonic_mode(args)
        return 0
    handle_puzzle_mode(args)
    code = run_only_mode(args)
    if code is not None:
        return code
    run_allinkeys(args)
    return 0


if __name__ == "__main__":
    import multiprocessing as mp

    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # already set

    vanity_path = find_vanitysearch_binary()
    if not vanity_path and os.name != "nt":
        try:
            subprocess.run(
                ["apt-get", "update"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "vanitysearch"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        vanity_path = find_vanitysearch_binary()
    if not vanity_path:
        raise FileNotFoundError(
            "VanitySearch binary not found. Please install VanitySearch."
        )
    else:
        print(f"✅ VanitySearch found: {vanity_path}", flush=True)

    sys.exit(main())
