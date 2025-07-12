# core/logger.py

import os
import datetime
import sys
import logging
import multiprocessing
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from config.settings import LOG_DIR, LOG_LEVEL, LOG_TO_CONSOLE, LOG_TO_FILE

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

log_queue: multiprocessing.Queue | None = None
_listener: QueueListener | None = None
_logger: logging.Logger | None = None

def _ensure_queue():
    global log_queue
    if log_queue is None:
        log_queue = multiprocessing.Queue(-1)
    return log_queue

def start_listener():
    """Start the multiprocessing log listener."""
    global _listener
    if _listener is not None:
        return _listener
    q = _ensure_queue()
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter('%(message)s')
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "debug.log"),
        maxBytes=250_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    handlers = [file_handler]
    if LOG_LEVEL in ("DEBUG", "INFO"):
        console_handler.setFormatter(fmt)
        handlers.append(console_handler)
    _listener = QueueListener(q, *handlers)
    _listener.start()
    return _listener

def stop_listener():
    global _listener
    if _listener:
        _listener.stop()
        _listener = None

def _get_logger():
    """Return a logger that sends records to the queue."""
    global _logger
    if _logger is None:
        _logger = logging.getLogger("allinkeys")
        _logger.setLevel(logging.DEBUG)
        qh = QueueHandler(_ensure_queue())
        _logger.addHandler(qh)
        _logger.propagate = False
    return _logger

# Path for the main rotating debug log
DEBUG_LOG_PATH = os.path.join(LOG_DIR, "debug.log")

def get_timestamp() -> str:
    """Return current timestamp in ``[YYYY-MM-DD HH:MM:SS]`` format."""
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

_LEVEL_MAP = {
    "TRACE": logging.DEBUG,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "ALERT": logging.ERROR,
}

def log_message(message: str, level: str = "INFO") -> None:
    """Send a log message through the shared logging queue."""
    if not (LOG_TO_CONSOLE or LOG_TO_FILE):
        return
    if level.upper() == "DEBUG" and LOG_LEVEL != "DEBUG":
        return
    timestamped = f"{get_timestamp()} {level.upper()}: {message}"
    logger = _get_logger()
    logger.log(_LEVEL_MAP.get(level.upper(), logging.INFO), timestamped)
