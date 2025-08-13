# core/logger.py

import os
import datetime
import sys
import time
import logging
import multiprocessing
from multiprocessing import queues as mp_queues
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from config.settings import LOG_DIR, LOG_LEVEL, LOG_TO_CONSOLE, LOG_TO_FILE


class SizeAndTimeRotatingFileHandler(RotatingFileHandler):
    """Rotate log files by size *or* age.

    The standard library offers separate handlers for size based and time
    based rotation.  For our use case we want both so that logs never grow
    beyond 10MB and are also refreshed at least once every 30 days.  This
    small helper subclasses :class:`RotatingFileHandler` and simply checks the
    modification time on each emit.
    """

    def __init__(self, filename: str, maxBytes: int, backupCount: int, max_days: int = 30, **kwargs):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, **kwargs)
        self.max_days = max_days

    def shouldRollover(self, record):  # type: ignore[override]
        if os.path.exists(self.baseFilename):
            # Trigger rotation if the file is older than ``max_days``
            mtime = os.path.getmtime(self.baseFilename)
            if time.time() - mtime > self.max_days * 86400:
                return 1
        return super().shouldRollover(record)


console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Use the actual Queue class for type hints to avoid runtime TypeError
log_queue: mp_queues.Queue | None = None
_listener: QueueListener | None = None
_logger: logging.Logger | None = None

def _ensure_queue():
    global log_queue
    if log_queue is None:
        log_queue = multiprocessing.Queue(-1)
    return log_queue

def initialize_logging(queue: mp_queues.Queue | None = None) -> mp_queues.Queue:
    """Initialize logging for a subprocess using the shared queue."""
    global log_queue, _logger
    if queue is not None:
        log_queue = queue
    elif log_queue is None:
        log_queue = multiprocessing.Queue(-1)
    _logger = None
    return log_queue

def start_listener():
    """Start the multiprocessing log listener.

    The listener fan-outs log records from a shared queue to multiple file
    handlers as well as stdout.  Each log level has its own rotating file to
    make troubleshooting easier.
    """

    global _listener
    if _listener is not None:
        return _listener

    q = _ensure_queue()
    os.makedirs(LOG_DIR, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Individual handlers per log level with rotation by size or age
    debug_handler = SizeAndTimeRotatingFileHandler(
        os.path.join(LOG_DIR, "debug.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(fmt)

    info_handler = SizeAndTimeRotatingFileHandler(
        os.path.join(LOG_DIR, "info.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(lambda r: r.levelno < logging.WARNING)
    info_handler.setFormatter(fmt)

    warning_handler = SizeAndTimeRotatingFileHandler(
        os.path.join(LOG_DIR, "warning.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    warning_handler.setLevel(logging.WARNING)
    warning_handler.addFilter(lambda r: r.levelno < logging.ERROR)
    warning_handler.setFormatter(fmt)

    error_handler = SizeAndTimeRotatingFileHandler(
        os.path.join(LOG_DIR, "error.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)

    # Dedicated vanity/keygen log handler to consolidate worker events
    vanity_handler = SizeAndTimeRotatingFileHandler(
        os.path.join(LOG_DIR, "vanity_worker.log"),
        maxBytes=25 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    vanity_handler.setLevel(logging.INFO)
    vanity_handler.setFormatter(fmt)
    # Only capture logs from vanity-related modules
    vanity_handler.addFilter(
        lambda r: r.name.startswith(
            ("core.keygen", "core.vanity_runner", "core.btc_only_checker", "core.altcoin_derive", "core.csv_checker")
        )
    )

    handlers = [debug_handler, info_handler, warning_handler, error_handler, vanity_handler]

    if LOG_TO_CONSOLE:
        console_handler.setLevel(logging.DEBUG if LOG_LEVEL == "DEBUG" else logging.INFO)
        console_handler.setFormatter(fmt)
        handlers.append(console_handler)

    _listener = QueueListener(q, *handlers, respect_handler_level=True)
    _listener.start()
    return _listener

def stop_listener():
    global _listener
    if _listener:
        _listener.stop()
        _listener = None

def get_logger(name: str = "allinkeys") -> logging.Logger:
    """Return a logger that sends records to the shared queue."""

    logger = logging.getLogger(name)
    if not any(isinstance(h, QueueHandler) for h in logger.handlers):
        qh = QueueHandler(_ensure_queue())
        logger.addHandler(qh)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger

# Path for the main rotating debug log
DEBUG_LOG_PATH = os.path.join(LOG_DIR, "debug.log")

def get_timestamp() -> str:
    """Return current timestamp in ``[YYYY-MM-DD HH:MM:SS]`` format."""
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

# Public alias for extremely verbose trace logging
# Maps directly to DEBUG so log handlers still capture the output.
TRACE = logging.DEBUG

_LEVEL_MAP = {
    "TRACE": TRACE,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "ALERT": logging.ERROR,
}

def log_message(message: str, level: str = "INFO", exc_info: bool = False) -> None:
    """Send a log message through the shared logging queue.

    ``exc_info=True`` will include the current exception stack trace in the
    log output which is vital for diagnosing failures in worker processes.
    """

    if not (LOG_TO_CONSOLE or LOG_TO_FILE):
        return
    if level.upper() == "DEBUG" and LOG_LEVEL != "DEBUG":
        return

    timestamped = f"{get_timestamp()} {level.upper()}: {message}"
    logger = get_logger()
    logger.log(_LEVEL_MAP.get(level.upper(), logging.INFO), timestamped, exc_info=exc_info)


# Backwards compatibility: some modules import ``_get_logger`` directly
_get_logger = get_logger


