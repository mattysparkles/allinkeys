import os
import sys
import threading
import multiprocessing
from multiprocessing import Process

from core.logger import get_logger, start_listener, stop_listener
from utils.thread_guard import can_spawn_thread

import config.settings as settings
from config.directories import LOG_DIR, DOWNLOAD_DIR

from core.btc_only_checker import btc_only_checker_loop
from core.downloader import download_and_compare_address_lists
from core.dashboard import init_dashboard_manager
from core.gpu_selector import assign_gpu_roles

from core.modes.vanity import metrics_updater

logger = get_logger(__name__)


def resolve_btc_compression(args):
    """Determine whether BTC addresses should be compressed."""
    if getattr(args, "puzzle", None) is not None:
        return True
    if getattr(args, "compressed", False):
        return True
    if getattr(args, "uncompressed", False):
        return False
    return getattr(args, "addr_format", "compressed") == "compressed"


def start(shared_metrics, args):
    coins = getattr(args, "only", None)
    if not coins:
        coins = ["btc"]
        args.only = coins

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
        assign_gpu_roles(getattr(args, "gpu_index", None))

        if shared_metrics is None:
            shared_metrics = init_dashboard_manager()
        shutdown_keygen = multiprocessing.Event()
        pause_keygen = multiprocessing.Event()
        shutdown_btc = multiprocessing.Event()
        pause_btc = multiprocessing.Event()
        shutdown_metrics = multiprocessing.Event()
        vanity_gpu_flag = multiprocessing.Value("i", 1)

        processes = []
        from core.logger import log_queue
        # Ensure telemetry also runs in BTC-only mode
        try:
            if settings.SEED_TELEMETRY_ENABLED and not getattr(args, "no_telemetry", False):
                from core.telemetry import start_telemetry, start_embedded_telemetry_service

                start_telemetry(shutdown_keygen)
                try:
                    svc_proc = start_embedded_telemetry_service()
                    if svc_proc is not None:
                        logger.info("[Started] Embedded telemetry service")
                        processes.append(svc_proc)
                except Exception as e:
                    logger.warning(f"Failed to start embedded telemetry service: {e}")
        except Exception:
            logger.warning("Telemetry initialization failed in BTC-only mode", exc_info=True)

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
            settings.ENABLE_DASHBOARD
            and not getattr(args, "no_dashboard", False)
            and not getattr(args, "headless", False)
        ):
            from ui.dashboard_gui import start_dashboard

            if can_spawn_thread("dashboard_launcher"):
                threading.Thread(target=start_dashboard, daemon=True).start()
            else:
                logger.warning("[ThreadGuard] Dashboard thread launch skipped")

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

    print(
        f"Warning: altcoin-only mode not fully implemented for: {', '.join(coins)}",
        file=sys.stderr,
    )
    if not getattr(args, "skip_downloads", False):
        download_and_compare_address_lists(coins=coins)
    return 1
