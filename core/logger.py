# core/logger.py

import os
import datetime
from config.settings import LOG_DIR, LOG_LEVEL, LOG_TO_CONSOLE, LOG_TO_FILE
import sys
import logging

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
console_handler.setStream(open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1))

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
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

def log_message(message, level="INFO"):
    """
    Logs a message to both console and log file depending on config flags.
    Level can be: INFO, DEBUG, WARN, ERROR, TRACE, ALERT, etc.
    """
    timestamped = f"{get_timestamp()} {level}: {message}"

    if LOG_TO_CONSOLE and level_severity(level) >= level_severity(LOG_LEVEL):
        print(timestamped)

    if LOG_TO_FILE and level in LOG_FILE_PATHS:
        try:
            with open(LOG_FILE_PATHS[level], "a", encoding="utf-8") as f:
                f.write(timestamped + "\n")
        except Exception as e:
            print(f"[ERROR] Failed to write log: {e}")

def level_severity(level):
    """
    Define severity levels for comparison.
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
