# core/logger.py

import os
import io
import datetime
import sys
import logging
from logging.handlers import RotatingFileHandler
from config.settings import LOG_DIR, LOG_LEVEL, LOG_TO_CONSOLE, LOG_TO_FILE

# stdout is configured in main

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

_debug_logger = None

def _get_debug_logger():
    global _debug_logger
    if _debug_logger is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        logger = logging.getLogger("allinkeys.debug")
        logger.setLevel(logging.DEBUG)
        handler = RotatingFileHandler(
            LOG_FILE_PATHS["DEBUG"], maxBytes=250_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(handler)
        logger.propagate = False
        _debug_logger = logger
    return _debug_logger

# Define separate log files by level
LOG_FILE_PATHS = {
    "INFO": os.path.join(LOG_DIR, "info.log"),
    "DEBUG": os.path.join(LOG_DIR, "debug.log"),
    "WARN": os.path.join(LOG_DIR, "warning.log"),
    "ERROR": os.path.join(LOG_DIR, "error.log"),
    "TRACE": os.path.join(LOG_DIR, "trace.log"),
    "ALERT": os.path.join(LOG_DIR, "alerts.log"),
    "CHECKING": os.path.join(LOG_DIR, "checking.log"),
    "SYSTEM": os.path.join(LOG_DIR, "system.log")
}

def get_timestamp():
    """
    Returns current timestamp in [YYYY-MM-DD HH:MM:SS] format.
    """
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

def level_severity(level):
    """
    Returns numeric severity for log level comparison.
    """
    levels = {
        "TRACE": 0,
        "DEBUG": 1,
        "INFO": 2,
        "WARN": 3,
        "ERROR": 4,
        "ALERT": 5
    }
    return levels.get(level.upper(), 99)

def log_message(message, level="INFO"):
    """
    Logs a message to both console and file if enabled.
    Supports: INFO, DEBUG, WARN, ERROR, TRACE, ALERT, etc.
    Uses UTF-8 for file output. Gracefully handles emoji printing to terminal.
    """
    timestamped = f"{get_timestamp()} {level}: {message}"

    # Print to console with fallback if terminal encoding breaks
    if LOG_TO_CONSOLE and level_severity(level) >= level_severity(LOG_LEVEL):
        try:
            print(timestamped, flush=True)
        except UnicodeEncodeError:
            fallback = timestamped.encode("ascii", errors="replace").decode()
            print(fallback, flush=True)

    # Write to file using a rotating handler for DEBUG, simple append otherwise
    if LOG_TO_FILE and level in LOG_FILE_PATHS:
        try:
            if level == "DEBUG":
                logger = _get_debug_logger()
                logger.debug(timestamped)
            else:
                with open(LOG_FILE_PATHS[level], "a", encoding="utf-8", errors="replace") as f:
                    f.write(timestamped + "\n")
        except Exception as e:
            try:
                print(f"[ERROR] Failed to write log: {e}", flush=True)
            except UnicodeEncodeError:
                print("[ERROR] Failed to write log: <encoding issue>", flush=True)
