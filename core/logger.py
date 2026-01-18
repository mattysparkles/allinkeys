# core/logger.py

import os
import datetime
import json
import sys
import logging
import multiprocessing
from multiprocessing import queues as mp_queues
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from config.settings import (
    LOG_LEVEL,
    LOG_FORMAT,
    LOG_TO_CONSOLE,
    LOG_TO_FILE,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
)
from config.directories import LOG_DIR
from utils.thread_guard import can_spawn_thread


console_handler = logging.StreamHandler(sys.stdout)

CORRELATION_FIELDS = (
    "batch_id",
    "index_within_batch",
    "gpu_id",
    "gpu_ids",
    "mode",
    "range_id",
    "endpoint",
)

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

    fmt = _get_formatter()

    # Individual handlers per log level
    debug_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "debug.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(fmt)

    info_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "info.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(lambda r: r.levelno < logging.WARNING)
    info_handler.setFormatter(fmt)

    warning_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "warning.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    warning_handler.setLevel(logging.WARNING)
    warning_handler.addFilter(lambda r: r.levelno < logging.ERROR)
    warning_handler.setFormatter(fmt)

    error_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "error.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)

    # Dedicated vanity/keygen log handler to consolidate worker events
    vanity_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "vanity_worker.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
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

    if not can_spawn_thread("log_listener"):
        logging.warning("[ThreadGuard] Log listener thread skipped; thread limit reached")
        return None
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
        logger.setLevel(_LEVEL_MAP.get(LOG_LEVEL.upper(), logging.INFO))
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

class JsonLogFormatter(logging.Formatter):
    """Emit log records as structured JSON with correlation fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        for field in CORRELATION_FIELDS:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        return json.dumps(payload, ensure_ascii=False)


def _normalized_log_format() -> str:
    return str(LOG_FORMAT or "text").strip().lower()


def _get_formatter() -> logging.Formatter:
    if _normalized_log_format() == "json":
        return JsonLogFormatter()
    return logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")


def _filter_context(context: dict) -> dict:
    return {k: v for k, v in context.items() if k in CORRELATION_FIELDS and v is not None}


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    *args,
    exc_info: bool = False,
    **context,
) -> None:
    level_value = _LEVEL_MAP.get(level.upper(), logging.INFO) if isinstance(level, str) else level
    extra = _filter_context(context)
    if extra:
        logger.log(level_value, message, *args, exc_info=exc_info, extra=extra)
    else:
        logger.log(level_value, message, *args, exc_info=exc_info)


def log_message(
    message: str,
    level: str = "INFO",
    exc_info: bool = False,
    **context,
) -> None:
    """Send a log message through the shared logging queue.

    ``exc_info=True`` will include the current exception stack trace in the
    log output which is vital for diagnosing failures in worker processes.
    """

    if not (LOG_TO_CONSOLE or LOG_TO_FILE):
        return
    if level.upper() == "DEBUG" and LOG_LEVEL != "DEBUG":
        return

    timestamped = f"{get_timestamp()} {level.upper()}: {message}"
    import inspect
    caller = inspect.currentframe().f_back  # type: ignore[assignment]
    module = caller.f_globals.get("__name__", "allinkeys") if caller else "allinkeys"
    logger = get_logger(module)
    if _normalized_log_format() == "json":
        log_with_context(logger, level, message, exc_info=exc_info, **context)
    else:
        log_with_context(logger, level, timestamped, exc_info=exc_info, **context)


# Backwards compatibility: some modules import ``_get_logger`` directly
_get_logger = get_logger
