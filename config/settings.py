"""
Master Configuration File for AllInKeys System
Auto-merged to restore full functionality.
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict

load_dotenv()


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def env_path(var: str, default: Path | str) -> Path:
    """Return a :class:`~pathlib.Path` from ``var`` or ``default``.

    Parameters
    ----------
    var:
        Name of the environment variable to read.
    default:
        Fallback path used when the environment variable is not set.

    The returned value is always converted to :class:`~pathlib.Path` and no
    filesystem interaction (such as directory creation) is performed here.
    """

    value = os.getenv(var)
    return Path(value) if value else Path(default)


# --------------------- API KEY ROTATION ---------------------
_API_KEY_STATES: Dict[str, Dict[str, object]] = {}


def _init_api_key(name: str) -> str:
    """Load API key(s) for ``name`` supporting comma-separated pools.

    The plural environment variable (``<NAME>S``) takes precedence and may
    contain a comma-separated list of keys.  If absent, the singular
    ``<NAME>`` is used.  The first key becomes the active value.
    """
    list_var = f"{name}S"
    keys = [k.strip() for k in os.getenv(list_var, "").split(",") if k.strip()]
    if not keys:
        single = os.getenv(name, "")
        keys = [single] if single else [""]
    _API_KEY_STATES[name] = {"keys": keys, "index": 0}
    os.environ[name] = keys[0]
    return keys[0]


def rotate_api_keys():
    """Advance to the next API key for all services."""
    for env_name, state in _API_KEY_STATES.items():
        state["index"] = (state["index"] + 1) % len(state["keys"])
        new_val = state["keys"][state["index"]]
        globals()[env_name] = new_val
        os.environ[env_name] = new_val
        if env_name == "TWILIO_AUTH_TOKEN":
            globals()["TWILIO_TOKEN"] = new_val
            os.environ["TWILIO_TOKEN"] = new_val

# ===================== 🔌 SYSTEM PATHS ==========================
# Root of the repository
BASE_DIR = env_path("ALLINKEYS_BASE_DIR", Path(__file__).resolve().parents[1])
# Directory for all log files
LOG_DIR = env_path("ALLINKEYS_LOG_DIR", BASE_DIR / "logs")
# Location where generated CSVs are stored
CSV_DIR = env_path("ALLINKEYS_CSV_DIR", BASE_DIR / "output" / "csv")
# Duplicate to keep legacy modules working
CSV_OUTPUT_DIR = env_path("ALLINKEYS_CSV_OUTPUT_DIR", CSV_DIR)
# Location for downloaded funded address lists
DOWNLOADS_DIR = env_path("ALLINKEYS_DOWNLOADS_DIR", BASE_DIR / "Downloads")
FULL_DIR = env_path("ALLINKEYS_FULL_DIR", DOWNLOADS_DIR / "full")
UNIQUE_DIR = env_path("ALLINKEYS_UNIQUE_DIR", DOWNLOADS_DIR / "unique")
# Where matches and encrypted alerts are archived
MATCHES_DIR = env_path("ALLINKEYS_MATCHES_DIR", BASE_DIR / "matches")
# VanitySearch text outputs
# ``ALLINKEYS_VANITY_OUTPUT_DIR`` is the newer variable while
# ``ALLINKEYS_VANITY_TXT_DIR`` is kept for backward compatibility.
# Legacy default: BASE_DIR/"vanity_output" when neither ALLINKEYS_VANITY_TXT_DIR nor
# ALLINKEYS_VANITY_OUTPUT_DIR is set (newer layout uses output/vanity_output).
_VANITY_DIR_DEFAULT = BASE_DIR / "vanity_output"
VANITY_TXT_DIR = env_path(
    "ALLINKEYS_VANITY_TXT_DIR",
    env_path("ALLINKEYS_VANITY_OUTPUT_DIR", _VANITY_DIR_DEFAULT),
)
VANITY_OUTPUT_DIR = VANITY_TXT_DIR  # legacy alias
# Mnemonic mode text outputs
MNEMONIC_TXT_DIR = env_path(
    "ALLINKEYS_MNEMONIC_TXT_DIR", BASE_DIR / "output" / "mnemonic_output"
)
# Local audio clips for alerts
SOUND_CLIPS_DIR = env_path("ALLINKEYS_SOUND_CLIPS_DIR", BASE_DIR / "alerts" / "sounds")
CHECKPOINT_PATH = env_path("ALLINKEYS_CHECKPOINT_PATH", LOG_DIR / "restore_checkpoint.json")
# Track which CSVs have been processed
CHECKED_CSV_LOG = env_path("ALLINKEYS_CHECKED_CSV_LOG", LOG_DIR / "checked_csvs.txt")
RECHECKED_CSV_LOG = env_path("ALLINKEYS_RECHECKED_CSV_LOG", LOG_DIR / "rechecked_csvs.txt")
# Track per-file progress for the CSV checker
CSV_CHECKPOINT_STATE = env_path("ALLINKEYS_CSV_CHECKPOINT_STATE", LOG_DIR / "csv_checker_state.json")
# Alias for backward compatibility
DOWNLOAD_DIR = DOWNLOADS_DIR
CHECKPOINT_FILE = env_path("ALLINKEYS_CHECKPOINT_FILE", BASE_DIR / "checkpoint.json")

# Number of days to keep downloaded files before purging
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))

# === BTC-only mode settings ===
ALL_BTC_ADDRESSES_URL = "https://alladdresses.loyce.club/all_Bitcoin_addresses_ever_used_sorted.txt.gz"
ALL_BTC_ADDRESSES_DIR = BASE_DIR / "all_btc_addresses"
ALL_BTC_RANGES_COUNT = 20
ALL_BTC_GZ_LOCAL = env_path(
    "ALLINKEYS_ALL_BTC_GZ_LOCAL",
    ALL_BTC_ADDRESSES_DIR / "all_Bitcoin_addresses_ever_used_sorted.txt.gz",
)
BTC_RANGE_FILE_PATTERN = "btc_range_{:02d}.txt"  # 00..19

# Backlog pause control (creation vs. consumption)
BACKLOG_PAUSE_THRESHOLD = int(
    os.getenv("BACKLOG_PAUSE_THRESHOLD", os.getenv("CHECKER_BACKLOG_PAUSE_THRESHOLD", "20000"))
)
BACKLOG_RESUME_THRESHOLD = int(
    os.getenv("BACKLOG_RESUME_THRESHOLD", "18000")
)
# Warning rate-limit (seconds), per event name
PAUSE_WARNING_RATELIMIT_SECONDS = int(
    os.getenv("PAUSE_WARNING_RATELIMIT_SECONDS", "30")
)

# Polling intervals for status updates
METRICS_POLL_INTERVAL_SECONDS = int(
    os.getenv("METRICS_POLL_INTERVAL_SECONDS", "3")
)
BACKLOG_MONITOR_INTERVAL_SECONDS = int(
    os.getenv("BACKLOG_MONITOR_INTERVAL_SECONDS", "2")
)

# Legacy alias for backward compatibility
CHECKER_BACKLOG_PAUSE_THRESHOLD = BACKLOG_PAUSE_THRESHOLD

# BTC-only processing stability settings
BTC_FILE_STABILITY_WINDOW_SEC = 3.0   # how long size must remain unchanged
BTC_FILE_STABILITY_POLLS = 6          # number of polls
BTC_FILE_STABILITY_INTERVAL_SEC = BTC_FILE_STABILITY_WINDOW_SEC / BTC_FILE_STABILITY_POLLS
BTC_MIN_FILE_AGE_SEC = 2.0            # ignore files newer than this

# --- VanitySearch Settings ---
VANITY_PATTERN = "1**"  # Change this pattern to match your target (e.g., starts with 1)


def find_vanitysearch_binary():
    """Return the first VanitySearch binary found for the host OS.

    ``VanitySearch`` ships as ``VanitySearch.exe`` on Windows but may be
    compiled without an extension on Linux.  Some environments (e.g. WSL)
    can execute either variant, so we search for both forms regardless of
    platform with OS-preferred names checked first.
    """
    bin_dir = os.path.join(BASE_DIR, "bin")

    exe_candidates = [
        os.path.join(bin_dir, "VanitySearch.exe"),
        os.path.join(bin_dir, "vanitysearch.exe"),
        os.path.join(bin_dir, "VanitySearch_cuda.exe"),
        os.path.join(bin_dir, "vanitysearch_cuda.exe"),
        "VanitySearch.exe",
        "vanitysearch.exe",
        "VanitySearch_cuda.exe",
        "vanitysearch_cuda.exe",
    ]
    nix_candidates = [
        os.path.join(bin_dir, "VanitySearch"),
        os.path.join(bin_dir, "vanitysearch"),
        os.path.join(bin_dir, "VanitySearch_cuda"),
        os.path.join(bin_dir, "vanitysearch_cuda"),
        "VanitySearch",
        "vanitysearch",
        "VanitySearch_cuda",
        "vanitysearch_cuda",
    ]

    # Windows prefers ``.exe`` binaries while POSIX environments prefer the
    # extensionless names.  Include both sets so running a Windows binary from
    # WSL (or vice-versa) still works.
    candidates = exe_candidates + nix_candidates if os.name == "nt" else nix_candidates + exe_candidates

    for cand in candidates:
        # Resolve absolute/relative candidates and ensure executability.
        path = cand if os.path.isabs(cand) else shutil.which(cand) or cand
        if not os.path.isfile(path):
            continue
        if os.name != "nt" and not os.access(path, os.X_OK):
            # Skip non-executable files on POSIX (e.g. bundled Windows .exe).
            continue
        return path
    return None


def find_oclvanity_binary(base_name: str):
    """Return path to ``base_name`` for the host OS."""
    bin_dir = os.path.join(BASE_DIR, "bin")
    if os.name == "nt":
        candidates = [
            os.path.join(bin_dir, f"{base_name}.exe"),
            os.path.join(bin_dir, f"{base_name}.EXE"),
            f"{base_name}.exe",
            f"{base_name}.EXE",
        ]
    else:
        candidates = [
            os.path.join(bin_dir, base_name),
            base_name,
        ]
    for cand in candidates:
        if os.path.isabs(cand) and os.path.isfile(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
        if os.path.isfile(cand):
            return cand
    return None


_vanitysearch = find_vanitysearch_binary()
VANITYSEARCH_PATH = Path(_vanitysearch) if _vanitysearch else None
# OpenCL/AMD variants from Vanitygen++
_oclvanitygen = find_oclvanity_binary("oclvanitygen")
OCLVANITYGEN_PATH = Path(_oclvanitygen) if _oclvanitygen else None
_oclvanityminer = find_oclvanity_binary("oclvanityminer")
OCLVANITYMINER_PATH = Path(_oclvanityminer) if _oclvanityminer else None
KEYCONV_PATH = env_path("ALLINKEYS_KEYCONV_PATH", BASE_DIR / "bin" / "keyconv.exe")
MAX_KEYS_PER_FILE = 100_000  #Deprecated
# Output file rotation config (for VanitySearch stream)
VANITY_ROTATE_LINES = 200_000
VANITY_MAX_BYTES = 500 * 1024 * 1024
MAX_OUTPUT_LINES = VANITY_ROTATE_LINES  # legacy alias
MAX_OUTPUT_FILE_SIZE = VANITY_MAX_BYTES  # legacy alias
USE_GPU = True
ROTATE_INTERVAL_SECONDS = 60
VANITY_MODE = "both"  # 'both' -> -b, 'uncompressed' -> -u, 'compressed' -> (no flag)

# ===================== ✅ ENABLED FEATURES ==========================
ENABLE_CHECKPOINT_RESTORE = True
ENABLE_CHECKPOINTING = True
CHECKPOINT_ENABLED = True
CHECKPOINT_INTERVAL_SECONDS = 180
MAX_CHECKPOINT_HISTORY = 3

# Toggle dashboard process
ENABLE_DASHBOARD = True
# Launch the Tkinter GUI
ENABLE_GUI = True
# Enable GPU/CPU key generation
ENABLE_KEYGEN = True
# Allow match alerts to be sent
ENABLE_ALERTS = True
# Convert vanitysearch backlog to CSV
ENABLE_BACKLOG_CONVERSION = True
# Initial day-one funded address checks
ENABLE_DAY_ONE_CHECKS = True
ENABLE_DAY_ONE_CHECK = ENABLE_DAY_ONE_CHECKS # Alias do not change
# Daily recheck of unique CSVs
ENABLE_DAILY_UNIQUE_RECHECK = True
ENABLE_UNIQUE_RECHECK = ENABLE_DAILY_UNIQUE_RECHECK # Alias do not change
# Derive altcoin addresses from generated keys
ENABLE_ALTCOIN_DERIVATION = True
ENABLE_SEED_VERIFICATION = False
# Encrypt matches using PGP
ENABLE_PGP = False
# Auto resume on crash/startup
ENABLE_AUTO_RESUME_DEPENDENCIES = True

# === Reference to this config file
CONFIG_FILE_PATH = __file__

# ===================== 🖼️ ASCII ART ==========================
LOGO_ART = r"""
  ______   __        __        ______  __    __        __    __  ________  __      __  ______  
 /      \ /  |      /  |      /      |/  \  /  |      /  |  /  |/        |/  \    /  |/      \ 
/$$$$$$  |$$ |      $$ |      $$$$$$/ $$  \ $$ |      $$ | /$$/ $$$$$$$$/ $$  \  /$$//$$$$$$  |
$$ |__$$ |$$ |      $$ |        $$ |  $$$  \$$ |      $$ |/$$/  $$ |__     $$  \/$$/ $$ \__$$/ 
$$    $$ |$$ |      $$ |        $$ |  $$$$  $$ |      $$  $$<   $$    |     $$  $$/  $$      \ 
$$$$$$$$ |$$ |      $$ |        $$ |  $$ $$ $$ |      $$$$$  \  $$$$$/       $$$$/    $$$$$$  |
$$ |  $$ |$$ |_____ $$ |_____  _$$ |_ $$ |$$$$ |      $$ |$$  \ $$ |_____     $$ |   /  \__$$ |
$$ |  $$ |$$       |$$       |/ $$   |$$ | $$$ |      $$ | $$  |$$       |    $$ |   $$    $$/ 
$$/   $$/ $$$$$$$$/ $$$$$$$$/ $$$$$$/ $$/   $$/       $$/   $$/ $$$$$$$$/     $$/     $$$$$$/  
"""
LOGO_ASCII = LOGO_ART


# ===================== 🔐 PGP SETTINGS ==========================
PGP_PUBLIC_KEY_PATH = env_path(
    "ALLINKEYS_PGP_PUBLIC_KEY_PATH", BASE_DIR / "sparkles_public_key.asc"
)

# ===================== 🎧 ALERT SETTINGS ==========================
ALERT_PHRASE = "The Beacons Have Been Lit, Gondor Calls for Aid!"
ENABLE_AUDIO_ALERT_LOCAL = True
ALERT_SOUND_FILE = env_path(
    "ALLINKEYS_ALERT_SOUND_FILE", SOUND_CLIPS_DIR / "gondor-calls-for-aid.mp3"
)
ENABLE_DESKTOP_WINDOW_ALERT = True
ALERT_POPUP_COLOR_1 = "#FF0000"
ALERT_POPUP_COLOR_2 = "#000000"

# ===================== 🔗 API KEYS ==========================
TOKENVIEW_API_KEY = os.getenv("TOKENVIEW_API_KEY", "")

# ===================== 🌍 COIN SOURCES ==========================
COIN_DOWNLOAD_URLS = {
    "btc": "https://addresses.loyce.club/Bitcoin_addresses_LATEST.txt.gz",
    "doge": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dogecoin/Latest_Dogecoin_Addresses.tsv.gz",
    "ltc": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Litecoin/Latest_Litecoin_Addresses.tsv.gz",
    "eth": "https://raw.githubusercontent.com/Pymmdrza/Rich-Address-Wallet/refs/heads/main/ETHEREUM/EthRich.txt",
    "bch": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/BitcoinCash/Latest_BitcoinCash_Addresses.tsv.gz",
    "dash": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dash/Latest_Dash_Addresses.tsv.gz"
}
MAX_DAILY_FILES_PER_COIN = 2
FILTER_ONLY_P2PKH = False

# Address generation toggles
ENABLE_P2PKH = True          # legacy "1" prefix (P2PKH)
# SegWit address generation toggles (bc1)
ENABLE_BC1_DEFAULT = False
ENABLE_BECH32_DEFAULT = ENABLE_BC1_DEFAULT  # deprecated alias
ENABLE_P2WPKH = ENABLE_BC1_DEFAULT         # bc1q… (Bech32 v0)
ENABLE_TAPROOT = ENABLE_BC1_DEFAULT        # bc1p… (Bech32m v1)

# GUI default patterns used when “All” selected
DEFAULT_BTC_PATTERNS = ["1**"]                # legacy
DEFAULT_BTC_PATTERNS_BECH32 = ["bc1q**"]      # v0
DEFAULT_BTC_PATTERNS_BECH32M = ["bc1p**"]     # v1

# Normalize bech32 case to lowercase (spec-compliant)
NORMALIZE_BECH32_LOWER = True

# ===================== 🔢 KEYGEN ==========================
USE_GPU = True
USE_CPU_FALLBACK = False
ROTATE_AT_MB = 100
ROTATE_AT_LINES = 200000
MAX_BATCH_SIZE = 100000
BATCH_SIZE = 100000
FILES_PER_BATCH = 5  # number of VanitySearch files per batch
ADDR_PER_FILE = 200000
START_BATCH_ID = 0
USE_CUSTOM_SEEDS = False
PATTERN = "1**"
VANITYSEARCH_GPU_INDEX = [0]
VANITY_GPU_INDEX = [0]

# ===================== GPU SCHEDULER ==========================

# These settings only apply when running the full `main.py` pipeline.
GPU_STRATEGY = "vanity_priority"  # Options: "vanity_priority", "csv_priority", "swing"
MAX_BACKLOG_THRESHOLD = 10  # backlog size to trigger GPU reassignment
MIN_BACKLOG_THRESHOLD = 1   # backlog size to resume vanity GPU keygen
GPU_VENDOR = "auto"  # "nvidia", "amd", or "auto"

# ===================== ALTCOIN ==========================
# Default to the first detected GPU for altcoin derivation. This avoids
# referencing a non-existent device which previously caused the process to
# fall back to CPU and produce no GPU-accelerated output.
ALTCOIN_GPUS_INDEX = [0]
CSV_MAX_SIZE_MB = 200
MAX_CSV_MB = CSV_MAX_SIZE_MB # alias do not change
CSV_MAX_ROWS = 200000
BCH_CASHADDR_ENABLED = True
EXCLUDE_ETH_FROM_DERIVE = False
ENABLED_COINS = {
    "BTC": True,
    "ETH": True,
    "DOGE": True,
    "LTC": True,
    "DASH": True,
    "BCH": True,
    "RVN": True,
    "PEP": True
}
# === Coin Toggle Shorthands ===
BTC = ENABLED_COINS["BTC"]
ETH = ENABLED_COINS["ETH"]
DOGE = ENABLED_COINS["DOGE"]
LTC = ENABLED_COINS["LTC"]
DASH = ENABLED_COINS["DASH"]
BCH = ENABLED_COINS["BCH"]
RVN = ENABLED_COINS["RVN"]
PEP = ENABLED_COINS["PEP"]

# ===================== 📊 DASHBOARD SETTINGS =======================
SHOW_BATCHES_COMPLETED = True
SHOW_CURRENT_SEED_INDEX = True
SHOW_CURRENT_SEED_INDEX = True
SHOW_CURRENT_SEED = True
SHOW_KEYS_PER_SEC = True
SHOW_AVG_KEYGEN_FILE_TIME = True
SHOW_AVG_CSV_FILE_CHECK_TIME = True
SHOW_CSV_CHECK_QUEUE_FILE_COUNT = True
SHOW_CSV_RECHECK_QUEUE_FILE_COUNT = True
SHOW_PROGRESS_BAR_CURRENT_CSV = True
SHOW_PROGRESS_BAR_CURRENT_CSV_RECHECK = True
SHOW_CPU_USAGE_STATS = True
SHOW_RAM_USAGE_STATS = True
SHOW_NVIDIA_GPU_STATS = True
SHOW_AMD_GPU_STATS = True
SHOW_BACKLOG_FILES_IN_QUEUE_COUNT = True
SHOW_BACKLOG_PROCESS_TIME_UNTIL_CAUGHT_UP = True
SHOW_AVERAGE_TIME_PER_BACKLOG_FILE = True
SHOW_PROGRESS_BAR_CURRENT_BACKLOG_FILENAME_PROCESSING = True
SHOW_CONTROL_BUTTONS_MAIN = True
SHOW_DISK_FREE = True
SHOW_BUTTONS_START_STOP_PAUSE_RESUME = True  # Shows main control buttons for the dashboard
SHOW_SAVE_DIRECTORIES = True
SHOW_UPTIME = True
SHOW_MATCHES_LIFETIME = True
SHOW_KEYS_GENERATED_TODAY = True
SHOW_KEYS_GENERATED_LIFETIME = True
SHOW_CSV_PROGRESS = True
SHOW_CSV_CREATED_TODAY = True
SHOW_CSV_CREATED_LIFETIME = True
SHOW_NEW_CSV_CHECKED_TODAY_TOTAL = True
SHOW_CSV_RECHECKED_TOTAL_TODAY = True
SHOW_ADDRESS_COUNTS_LIFETIME = True  # Show total addresses created lifetime (per coin)
SHOW_ADDRESS_CREATED_COUNTS_TODAY = True  # Show total addresses created today (per coin)
SHOW_ADDRESS_CHECKED_COUNTS_TODAY = True
SHOW_ADDRESS_CHECKED_COUNTS_LIFETIME = True

ADDRESS_CREATED_TODAY = {
    "btc": True,
    "doge": True,
    "dash": True,
    "ltc": True,
    "bch": True,
    "rvn": True,
    "pep": True,
    "eth": True,
}
ADDRESS_CREATED_LIFETIME = ADDRESS_CREATED_TODAY.copy()
ADDRESS_CHECKED_TODAY = ADDRESS_CREATED_TODAY.copy()
ADDRESS_CHECKED_LIFETIME = ADDRESS_CREATED_TODAY.copy()

SHOW_ALERTS_SUCCESSFULLY_CONFIGURED_TYPES = True
SHOW_ALERT_TYPE_SELECTOR_CHECKBOXES = True

# ===================== BUTTON CONTROLS ==========================
VANITY_SEARCH_BUTTON_CONTROL = True
VANITY_SEARCH_START_BUTTON = True
VANITY_SEARCH_STOP_BUTTON = True
VANITY_SEARCH_PAUSE_BUTTON = True
VANITY_SEARCH_RESUME_BUTTON = True

ALTCOIN_BUTTON_CONTROL = True
ALTCOIN_START_BUTTON = True
ALTCOIN_STOP_BUTTON = True
ALTCOIN_PAUSE_BUTTON = True
ALTCOIN_RESUME_BUTTON = True

CSV_CHECK_BUTTON_CONTROL = True
CSV_CHECK_START_BUTTON = True
CSV_CHECK_STOP_BUTTON = True
CSV_CHECK_PAUSE_BUTTON = True
CSV_CHECK_RESUME_BUTTON = True

CSV_RECHECK_BUTTON_CONTROL = True
CSV_RECHECK_START_BUTTON = True
CSV_RECHECK_STOP_BUTTON = True
CSV_RECHECK_PAUSE_BUTTON = True
CSV_RECHECK_RESUME_BUTTON = True

ALERTS_BUTTON_CONTROL = True
ALERTS_START_BUTTON = True
ALERTS_STOP_BUTTON = True
ALERTS_PAUSE_BUTTON = True
ALERTS_RESUME_BUTTON = True

OPEN_CONFIG_FILE_FROM_DASHBOARD = True
SHOW_REFRESH_DASHBOARD_DATA_BUTTON = True
SHOW_DELETE_DASHBOARD_DATA_BUTTON = True

DELETE_VANITY_SEARCH_LOGS = True
DELETE_CSV_FILES = True
DELETE_SYSTEM_LOGS = True
DELETE_CSV_CHECKING_LOGS = True

# ===================== 📜 LOGGING ================================
LOG_LEVEL = "INFO" # Options include: INFO, DEBUG, TRACE,
LOG_TO_FILE = True
LOG_TO_CONSOLE = True
LOGGING_ENABLED = True  # or False if you want to disable it
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


# ===================== 🔒 SECURITY ==========================
# The dashboard password is stored as a salted hash to avoid keeping
# plaintext secrets in memory or on disk.  ``DASHBOARD_PASSWORD_HASH`` can be
# supplied via environment variable or ``--dashboard-password`` CLI flag.
DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "")
DELETE_CONFIRMATION_PASSWORD = os.getenv("DELETE_CONFIRMATION_PASSWORD", "")
# Backward compatible alias used by the GUI module
DASHBOARD_PASSWORD = DASHBOARD_PASSWORD_HASH

# ===================== ❤️ DONATION INFO ==========================
SHOW_DONATION_MESSAGE = True
DONATION_ADDRESSES = {
    "BTC": "18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y",
    "DOGE": "DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5",
    "LTC": "LNmgLkonXtecopmGauqsDFvci4XQTZAWmg",
    "ETH_BSC_ERC-20": "0xCb8B2937D60c47438562A2E53d08B85865B57741",
    "XRP": "rNEq4vB5yAKNj52UzNwok4TJKSQuHXQNnc",
    "XMR": "43DUJ1MA7Mv1n4BTRHemEbDmvYzMysVt2djHnjGzrHZBb4WgMDtQHWh51ZfbcVwHP8We6pML4f1Q7SNEtveYCk4HDdb14ik",
    "SOL": "wNR4sffGQwvK4vh6cgxPhhoN71wQT5gdn2Ksy7ueBYa",
    "ADA": "addr1qye3f4jszpwcdwz2dzn8lcgjjfsllfyrd7zypmmjx9h6a3nyuw3zpuku8w3kpe47t83pgd8tq4yz9sqndxyv4g2py8nsseve6s",
    "DASH": "XrHT9dWzXW3yxcyeUQKhc9yocTFw2iFj3b",
    "RVN": "R9StG74J6q15iyxvXySEghF7FbKKJBKRQB",
    "ZEC": "t1RBJ6BVrPuiZ5Gq2Wh8SAMkSSK9aqd3xvh",
    "BTG": "GRt4a119DHFSN9oGGw1tGwUzg5qtNCprCH",
    "PEP": "PbCiPTNrYaCgv1aqNCds5n7Q73znGrTkgp",
    "BCH_BSV": "bitcoincash:qpnyvtz65u9nf4ddd0wewjrge4jedu7l2sayuy09fw",
    "XLM": "GBGMRI6Z3JFMEZSUSZROASNLWOIDLRAUEX5RNAVCAFC7A52X5HCG5UYJ"
}

# ===================== 🔔 ALERTS + NOTIFICATIONS ====================

ENABLE_ALERTS = True  # Master toggle

# Hide sensitive information like seeds or private keys in outgoing alert bodies
REDACT_SENSITIVE_DATA_IN_ALERTS = True

# === LOCAL AUDIO ALERT ===
ENABLE_AUDIO_ALERT_LOCAL = True
ALERT_SOUND_FILE = env_path(
    "ALLINKEYS_ALERT_SOUND_FILE", SOUND_CLIPS_DIR / "gondor-calls-for-aid.mp3"
)  # Must exist or alert will be skipped

# === DESKTOP POP-UP WINDOW ALERT ===
ENABLE_DESKTOP_WINDOW_ALERT = True
ALERT_POPUP_COLOR_1 = "#FF0000"  # First flash color
ALERT_POPUP_COLOR_2 = "#000000"  # Second flash color
ALERT_PHRASE = "The Beacons Have Been Lit, Gondor Calls for Aid!"  # Message shown in window

# === PGP ENCRYPTED MATCH ALERT OUTPUT ===
ENABLE_PGP = False
PGP_PUBLIC_KEY_PATH = env_path(
    "ALLINKEYS_PGP_PUBLIC_KEY_PATH",
    BASE_DIR / "Sparkles-allinkeys_0x3A94D30E_public.asc",
)  # Must be a valid ASCII armored key file

# === EMAIL ALERT CONFIGURATION ===
ALERT_EMAIL_ENABLED = True
ALERT_EMAIL_SENDER = os.getenv("ALERT_EMAIL_SENDER", "emailsenderbtc@gmail.com")
ALERT_EMAIL_PASSWORD = os.getenv("ALERT_EMAIL_PASSWORD", "")
ALERT_EMAIL_RECIPIENTS = os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",") if os.getenv("ALERT_EMAIL_RECIPIENTS") else []
EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", 587))
INCLUDE_MATCH_INFO = True
ENCRYPTED_MESSAGE = False
# SMTP Credentials (required if ALERT_EMAIL_ENABLED is True)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")           # Or use your provider's SMTP host
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))                          # TLS port (use 465 for SSL)
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "emailsenderbtc@gmail.com")        # Replace with your actual sending email
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")    # App password if using Gmail 2FA
ALERT_EMAIL_FROM = SMTP_USERNAME  # or hardcode like "you@example.com"
ALERT_EMAIL_TO = ALERT_EMAIL_RECIPIENTS  # DONT CHANGE HERE CHANGE ALERT_EMAIL_RECIPIENTS OPTION ABOVE


# === TELEGRAM BOT ALERT CONFIGURATION ===
ALERT_TELEGRAM_ENABLED = True
ENABLE_TELEGRAM_ALERT = ALERT_TELEGRAM_ENABLED # alias for backward compatibility dont modify
TELEGRAM_BOT_TOKEN = _init_api_key("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# === SMS VIA TWILIO ===
ALERT_SMS_ENABLED = True
ENABLE_SMS_ALERT = ALERT_SMS_ENABLED # alias for backward compatibility dont modify
TWILIO_SID = _init_api_key("TWILIO_SID")
TWILIO_AUTH_TOKEN = _init_api_key("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER = os.getenv("TWILIO_TO_NUMBER", "")
TWILIO_TO = TWILIO_TO_NUMBER # Alias do not change
TWILIO_TO_SMS = TWILIO_TO_NUMBER  # alias for backward compatibility
TWILIO_FROM = TWILIO_FROM_NUMBER  # alias for backward compatibility
ENABLE_PHONE_CALL_ALERT = True
TWILIO_CALL_TO_NUMBER = os.getenv("TWILIO_CALL_TO_NUMBER", "")
TWILIO_TOKEN = TWILIO_AUTH_TOKEN  # Alias do not change
TWILIO_TO_CALL = TWILIO_CALL_TO_NUMBER # Alias do not change

# === DISCORD WEBHOOK ALERTS ===
ALERT_DISCORD_ENABLED = False
ENABLE_DISCORD_ALERT = ALERT_DISCORD_ENABLED # Alias do not change
DISCORD_WEBHOOK_URL = _init_api_key("DISCORD_WEBHOOK_URL")

# === HOME ASSISTANT / IoT WEBHOOK ===
ALERT_HOME_ASSISTANT_ENABLED = False
ENABLE_HOME_ASSISTANT_ALERT = ALERT_HOME_ASSISTANT_ENABLED # Alias do not change
HOME_ASSISTANT_WEBHOOK = os.getenv("HOME_ASSISTANT_WEBHOOK", "")
HOME_ASSISTANT_URL = HOME_ASSISTANT_WEBHOOK # Alias do not change
HOME_ASSISTANT_TOKEN = _init_api_key("HOME_ASSISTANT_TOKEN")

# === CLOUD STORAGE MATCH BACKUPS ===

# iCloud
ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE = False
ICLOUD_LOGIN = os.getenv("ICLOUD_LOGIN", "you@icloud.com")
ICLOUD_PASSWORD = os.getenv("ICLOUD_PASSWORD", "")
ICLOUD_DRIVE_PATH = os.getenv("ICLOUD_DRIVE_PATH", "/path/on/icloud")
ENABLE_CLOUD_UPLOAD = ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE # Alias do not change

# Google Drive
ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE = False
GOOGLE_DRIVE_LOGIN = os.getenv("GOOGLE_DRIVE_LOGIN", "you@gmail.com")
GOOGLE_DRIVE_PASSWORD = os.getenv("GOOGLE_DRIVE_PASSWORD", "")
GOOGLE_DRIVE_FILE_PATH = os.getenv("GOOGLE_DRIVE_FILE_PATH", "/path/on/gdrive")

# Dropbox
ALERT_SAVE_MATCHES_TO_DROPBOX = False
DROPBOX_LOGIN = os.getenv("DROPBOX_LOGIN", "you@protonmail.com")
DROPBOX_PASSWORD = os.getenv("DROPBOX_PASSWORD", "")
DROPBOX_FILE_PATH = os.getenv("DROPBOX_FILE_PATH", "/dropbox/folder")

# === LOCAL MATCH FILE SAVE ===
ALERT_SAVE_MATCHES_TO_LOCAL_FILE = True
FILE_PATH = MATCHES_DIR  # Matches folder
MATCH_LOG_DIR = MATCHES_DIR # Alias do not change
INCLUDE_MATCH_INFO = True
ENCRYPTED_MESSAGE = False

# === Coin Toggle Shorthands ===
BTC = ENABLED_COINS["BTC"]
ETH = ENABLED_COINS["ETH"]
DOGE = ENABLED_COINS["DOGE"]
LTC = ENABLED_COINS["LTC"]
DASH = ENABLED_COINS["DASH"]
BCH = ENABLED_COINS["BCH"]
RVN = ENABLED_COINS["RVN"]
PEP = ENABLED_COINS["PEP"]


# ===================== 🕒 TIMESTAMP ==========================
LAUNCH_TIMESTAMP = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# Timezone configuration for daily metric resets
ENABLE_AUTO_TIMEZONE_SETTING = True
MANUAL_TIME_ZONE_OVERRIDE = "UTC-5"

# ===================== 📈 STATISTICS TO DISPLAY MAPPING =======================
STATS_TO_DISPLAY = {
    "keys_per_sec": SHOW_KEYS_PER_SEC,
    "batches_completed": SHOW_BATCHES_COMPLETED,
    "current_seed_index": SHOW_CURRENT_SEED_INDEX,
    "current_seed": SHOW_CURRENT_SEED,
    "avg_keygen_time": SHOW_AVG_KEYGEN_FILE_TIME,
    "avg_check_time": SHOW_AVG_CSV_FILE_CHECK_TIME,
    "cpu_usage": SHOW_CPU_USAGE_STATS,
    "ram_usage": SHOW_RAM_USAGE_STATS,
    "disk_free_gb": SHOW_DISK_FREE,
    "disk_fill_eta": SHOW_DISK_FREE,
    "uptime": SHOW_UPTIME,
    "csv_checked_today": SHOW_NEW_CSV_CHECKED_TODAY_TOTAL,
    "csv_rechecked_today": SHOW_CSV_RECHECKED_TOTAL_TODAY,
    "matches_found_lifetime": SHOW_MATCHES_LIFETIME,
    "keys_generated_today": SHOW_KEYS_GENERATED_TODAY,
    "keys_generated_lifetime": SHOW_KEYS_GENERATED_LIFETIME,
    "mnemonics_generated_today": True,
    "mnemonics_generated_lifetime": True,
    "vanity_progress_percent": SHOW_KEYS_PER_SEC,
    "csv_created_today": SHOW_CSV_CREATED_TODAY,
    "csv_created_lifetime": SHOW_CSV_CREATED_LIFETIME,
    "altcoin_files_converted": True,
    "derived_addresses_today": True,
    "alerts_sent_today": True,
    "addresses_checked_today": SHOW_ADDRESS_CHECKED_COUNTS_TODAY,
    "addresses_checked_lifetime": SHOW_ADDRESS_CHECKED_COUNTS_LIFETIME,
    "backlog_files_queued": SHOW_BACKLOG_FILES_IN_QUEUE_COUNT,
    "backlog_eta": SHOW_BACKLOG_PROCESS_TIME_UNTIL_CAUGHT_UP,
    "backlog_avg_time": SHOW_AVERAGE_TIME_PER_BACKLOG_FILE,
    "backlog_current_file": SHOW_PROGRESS_BAR_CURRENT_BACKLOG_FILENAME_PROCESSING,
    "vanity_backlog_count": True,
    "new_btc_ranges_size_bytes": True,
    "btc_ranges_progress": True,
    "btc_ranges_last_updated": True,
    "btc_ranges_files_ready": True,
    "btc_ranges_updated_today": True,
    "download_progress": True,
    "btc_only_files_checked_today": True,
    "btc_only_matches_found_today": True,
    "vanitysearch_current_mkeys": True,
    "vanitysearch_backend": True,
    "vanitysearch_device_name": True,
    "csv_checker": True,
    "gpu_stats": True,
    "gpu_assignments": True,
    "gpu_strategy": True,
    "gpu_assignment": True,
    "vanity_gpu_on": True,
    "altcoin_gpu_on": True,
    "status": True,
    "last_updated": True,
}
# ===================== ⏱️ DASHBOARD REFRESH ==========================
DASHBOARD_REFRESH_INTERVAL = 1.0  # seconds between dashboard UI updates

# ===================== 📋 DASHBOARD METRIC LABELS ==========================
# Human friendly names for dashboard metrics
METRICS_LABEL_MAP = {
    "keys_per_sec": "Keys/sec",
    "batches_completed": "Batches Completed",
    "current_seed_index": "Current Seed Index",
    "current_seed": "Current Seed",
    "avg_keygen_time": "Avg. Keygen Time",
    "avg_check_time": "Avg. CSV Check Time",
    "cpu_usage": "CPU Usage",
    "ram_usage": "RAM Usage",
    "disk_free_gb": "Disk Free (GB)",
    "disk_fill_eta": "Disk Fill ETA",
    "uptime": "Uptime",
    "csv_checked_today": "Day-One Checked",
    "csv_rechecked_today": "Unique Rechecked",
    "csv_created_today": "CSVs Today",
    "csv_created_lifetime": "CSVs Lifetime",
    "altcoin_files_converted": "Converted CSVs",
    "derived_addresses_today": "Total Derived Addresses",
    "alerts_sent_today": "Alerts Sent",
    "matches_found_lifetime": "Matches Lifetime",
    "keys_generated_today": "Keys Generated Today",
    "keys_generated_lifetime": "Keys Generated Lifetime",
    "mnemonics_generated_today": "Mnemonics Generated Today",
    "mnemonics_generated_lifetime": "Mnemonics Generated Lifetime",
    "vanity_progress_percent": "Keygen Progress %",
    "addresses_checked_today": "Addresses Checked Today",
    "addresses_checked_lifetime": "Addresses Checked Lifetime",
    "backlog_files_queued": "Backlog Queue",
    "backlog_eta": "Backlog ETA",
    "backlog_avg_time": "Avg. Backlog Time",
    "backlog_current_file": "Current Backlog File",
    "vanity_backlog_count": "Vanity Backlog",
    "new_btc_ranges_size_bytes": "New BTC Ranges Size (bytes)",
    "btc_ranges_progress": "BTC Ranges Progress",
    "btc_ranges_last_updated": "BTC Ranges Last Updated",
    "btc_ranges_files_ready": "BTC Ranges Ready",
    "btc_ranges_updated_today": "BTC Ranges Updated",
    "download_progress": "Download Progress",
    "btc_only_files_checked_today": "BTC Files Checked Today",
    "btc_only_matches_found_today": "BTC Matches Today",
    "vanitysearch_current_mkeys": "VanitySearch MKeys/s",
    "vanitysearch_backend": "VanitySearch Backend",
    "vanitysearch_device_name": "VanitySearch Device",
    "csv_checker": "CSV Checker",
    "gpu_stats": "GPU Stats",
    "gpu_assignments": "GPU Assignments",
    "gpu_strategy": "Current GPU Strategy",
    "gpu_assignment": "Active Assignment",
    "vanity_gpu_on": "Vanity GPU",
    "altcoin_gpu_on": "Altcoin Derive GPU",
    "status": "Module Status",
    "last_updated": "Last Updated",
}
# ===================== ⚠️ ALERT CONFIG OPTIONS FOR GUI ======================
ALERT_OPTIONS = {
    "AUDIO_LOCAL": ENABLE_AUDIO_ALERT_LOCAL,
    "DESKTOP_WINDOW": ENABLE_DESKTOP_WINDOW_ALERT,
    "PGP_ENCRYPTED": ENABLE_PGP,
    "EMAIL": ALERT_EMAIL_ENABLED,
    "TELEGRAM": ALERT_TELEGRAM_ENABLED,
    "SMS": ALERT_SMS_ENABLED,
    "DISCORD": ALERT_DISCORD_ENABLED,
    "ICLOUD": ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE,
    "GOOGLE_DRIVE": ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE,
    "DROPBOX": ALERT_SAVE_MATCHES_TO_DROPBOX,
    "LOCAL_FILE": ALERT_SAVE_MATCHES_TO_LOCAL_FILE,
    "HOME_ASSISTANT": ALERT_HOME_ASSISTANT_ENABLED,
}
# ===================== ✅ ALERT CHECKBOX TOGGLES FOR GUI ======================
ALERT_CHECKBOXES = {
    "ENABLE_AUDIO_ALERT_LOCAL": ENABLE_AUDIO_ALERT_LOCAL,
    "ENABLE_DESKTOP_WINDOW_ALERT": ENABLE_DESKTOP_WINDOW_ALERT,
    "ENABLE_PGP": ENABLE_PGP,
    "ALERT_EMAIL_ENABLED": ALERT_EMAIL_ENABLED,
    "ALERT_TELEGRAM_ENABLED": ALERT_TELEGRAM_ENABLED,
    "ALERT_SMS_ENABLED": ALERT_SMS_ENABLED,
    "ENABLE_SMS_ALERT": ENABLE_SMS_ALERT,
    "ENABLE_PHONE_CALL_ALERT": ENABLE_PHONE_CALL_ALERT,
    "ALERT_DISCORD_ENABLED": ALERT_DISCORD_ENABLED,
    "ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE": ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE,
    "ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE": ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE,
    "ALERT_SAVE_MATCHES_TO_DROPBOX": ALERT_SAVE_MATCHES_TO_DROPBOX,
    "ALERT_SAVE_MATCHES_TO_LOCAL_FILE": ALERT_SAVE_MATCHES_TO_LOCAL_FILE,
    "ALERT_HOME_ASSISTANT_ENABLED": ALERT_HOME_ASSISTANT_ENABLED,
}
# ===================== ⚠️ ALERT CREDENTIAL WARNINGS ======================
ALERT_CREDENTIAL_WARNINGS = {
    "ALERT_EMAIL_ENABLED": not all([
        'ALERT_EMAIL_SENDER' in globals(),
        'ALERT_EMAIL_PASSWORD' in globals(),
        'ALERT_EMAIL_RECIPIENTS' in globals()
    ]),
    "ALERT_TELEGRAM_ENABLED": not all([
        'TELEGRAM_BOT_TOKEN' in globals(),
        'TELEGRAM_CHAT_ID' in globals()
    ]),
    "ALERT_SMS_ENABLED": not all([
        'TWILIO_SID' in globals(),
        'TWILIO_TOKEN' in globals(),
        'TWILIO_FROM' in globals(),
        'TWILIO_TO_SMS' in globals()
    ]),
    "ENABLE_SMS_ALERT": not all([
        'TWILIO_SID' in globals(),
        'TWILIO_TOKEN' in globals(),
        'TWILIO_FROM' in globals(),
        'TWILIO_TO_SMS' in globals()
    ]),
    "ENABLE_PHONE_CALL_ALERT": not all([
        'TWILIO_SID' in globals(),
        'TWILIO_TOKEN' in globals(),
        'TWILIO_FROM' in globals(),
        'TWILIO_TO_CALL' in globals()
    ]),
    "ALERT_DISCORD_ENABLED": not ('DISCORD_WEBHOOK_URL' in globals()),
    "ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE": not all([
        'ICLOUD_LOGIN' in globals(),
        'ICLOUD_PASSWORD' in globals(),
        'ICLOUD_DRIVE' in globals()
    ]),
    "ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE": not all([
        'GOOGLE_DRIVE_LOGIN' in globals(),
        'GOOGLE_DRIVE_PASSWORD' in globals(),
        'GOOGLE_DRIVE_FILE_PATH' in globals()
    ]),
    "ALERT_SAVE_MATCHES_TO_DROPBOX": not all([
        'DROPBOX_LOGIN' in globals(),
        'DROPBOX_PASSWORD' in globals(),
        'DROPBOX_FILE_PATH' in globals()
    ]),
    "ALERT_HOME_ASSISTANT_ENABLED": not ('HOME_ASSISTANT_WEBHOOK' in globals())
}


# ===================== 🕹️ BUTTONS ENABLED STATE MAP ==========================
BUTTONS_ENABLED = {
    "vanity": SHOW_BUTTONS_START_STOP_PAUSE_RESUME,
    "altcoin": ALTCOIN_BUTTON_CONTROL,
    "csv_check": CSV_CHECK_BUTTON_CONTROL,
    "csv_recheck": CSV_RECHECK_BUTTON_CONTROL,
    "alerts": ALERTS_BUTTON_CONTROL
}

# ===================== 🖥️ GPU/CPU BACKENDS ==========================
# GPU/CPU selection & binaries
# Only the CUDA-enabled VanitySearch binary is bundled.
GPU_BACKEND = os.getenv("GPU_BACKEND", "cuda")  # cuda, opencl, cpu, auto, or oclvanitygen
VANITYSEARCH_BIN_CUDA = VANITYSEARCH_PATH or ""
VANITYSEARCH_BIN_OPENCL = ""  # placeholder for future OpenCL support
VANITYSEARCH_BIN_CPU = VANITYSEARCH_PATH or ""  # CPU fallback shares the same binary

FORCE_CPU_FALLBACK = False  # If True, run CPU even if GPU available
MIN_EXPECTED_GPU_MKEYS = 120.0  # GTX 1060 typical: 150–230 MKeys/s

# Alerts – ensure Twilio CALL enabled
ENABLE_SMS_ALERT = ENABLE_SMS_ALERT
ENABLE_PHONE_CALL_ALERT = ENABLE_PHONE_CALL_ALERT

# PGP
ENABLE_PGP_ENCRYPTION = False
PGP_RECIPIENT = ""          # key email or uid fragment
PGP_KEYRING_PATH = r"P:\\ALLINKEYS\\pgp\\pubring.kbx"  # ok if empty; use default


