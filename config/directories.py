"""Filesystem locations extracted from ``config.settings``."""

from __future__ import annotations

from pathlib import Path
import sys

from .environment import env_path

BASE_DIR = env_path("ALLINKEYS_BASE_DIR", Path(__file__).resolve().parents[1])
ASSETS_DIR = env_path(
    "ALLINKEYS_ASSETS_DIR",
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR,
)
LOG_DIR = env_path("ALLINKEYS_LOG_DIR", BASE_DIR / "logs")
CSV_DIR = env_path("ALLINKEYS_CSV_DIR", BASE_DIR / "output" / "csv")
CSV_OUTPUT_DIR = env_path("ALLINKEYS_CSV_OUTPUT_DIR", CSV_DIR)
DOWNLOADS_DIR = env_path("ALLINKEYS_DOWNLOADS_DIR", BASE_DIR / "Downloads")
FULL_DIR = env_path("ALLINKEYS_FULL_DIR", DOWNLOADS_DIR / "full")
UNIQUE_DIR = env_path("ALLINKEYS_UNIQUE_DIR", DOWNLOADS_DIR / "unique")
MATCHES_DIR = env_path("ALLINKEYS_MATCHES_DIR", BASE_DIR / "matches")

_VANITY_DIR_DEFAULT = BASE_DIR / "output" / "vanity_output"
VANITY_OUTPUT_DIR = env_path(
    "ALLINKEYS_VANITY_OUTPUT_DIR",
    env_path("ALLINKEYS_VANITY_TXT_DIR", _VANITY_DIR_DEFAULT),
)
MNEMONIC_TXT_DIR = env_path(
    "ALLINKEYS_MNEMONIC_TXT_DIR", BASE_DIR / "output" / "mnemonic_output"
)
SOUND_CLIPS_DIR = env_path(
    "ALLINKEYS_SOUND_CLIPS_DIR", ASSETS_DIR / "alerts" / "sounds"
)
CHECKPOINT_PATH = env_path(
    "ALLINKEYS_CHECKPOINT_PATH", LOG_DIR / "restore_checkpoint.json"
)
CHECKED_CSV_LOG = env_path("ALLINKEYS_CHECKED_CSV_LOG", LOG_DIR / "checked_csvs.txt")
RECHECKED_CSV_LOG = env_path(
    "ALLINKEYS_RECHECKED_CSV_LOG", LOG_DIR / "rechecked_csvs.txt"
)
CSV_CHECKPOINT_STATE = env_path(
    "ALLINKEYS_CSV_CHECKPOINT_STATE", LOG_DIR / "csv_checker_state.json"
)
DOWNLOAD_DIR = DOWNLOADS_DIR
CHECKPOINT_FILE = env_path("ALLINKEYS_CHECKPOINT_FILE", BASE_DIR / "checkpoint.json")

ALL_BTC_ADDRESSES_DIR = BASE_DIR / "all_btc_addresses"
ALL_BTC_GZ_LOCAL = env_path(
    "ALLINKEYS_ALL_BTC_GZ_LOCAL",
    ALL_BTC_ADDRESSES_DIR / "all_Bitcoin_addresses_ever_used_sorted.txt.gz",
)

KEYCONV_PATH = env_path("ALLINKEYS_KEYCONV_PATH", ASSETS_DIR / "bin" / "keyconv.exe")
PGP_PUBLIC_KEY_PATH = env_path(
    "ALLINKEYS_PGP_PUBLIC_KEY_PATH", ASSETS_DIR / "sparkles_public_key.asc"
)
ALERT_SOUND_FILE = env_path(
    "ALLINKEYS_ALERT_SOUND_FILE", SOUND_CLIPS_DIR / "gondor-calls-for-aid.mp3"
)

__all__ = [
    "BASE_DIR",
    "ASSETS_DIR",
    "LOG_DIR",
    "CSV_DIR",
    "CSV_OUTPUT_DIR",
    "DOWNLOADS_DIR",
    "FULL_DIR",
    "UNIQUE_DIR",
    "MATCHES_DIR",
    "VANITY_OUTPUT_DIR",
    "MNEMONIC_TXT_DIR",
    "SOUND_CLIPS_DIR",
    "CHECKPOINT_PATH",
    "CHECKED_CSV_LOG",
    "RECHECKED_CSV_LOG",
    "CSV_CHECKPOINT_STATE",
    "DOWNLOAD_DIR",
    "CHECKPOINT_FILE",
    "ALL_BTC_ADDRESSES_DIR",
    "ALL_BTC_GZ_LOCAL",
    "KEYCONV_PATH",
    "PGP_PUBLIC_KEY_PATH",
    "ALERT_SOUND_FILE",
]
