"""
Master Configuration File for AllInKeys System
Auto-merged to restore full functionality.
"""

import os
from datetime import datetime
# --- Paths ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT_DIR, "logs")
...
# ===================== 🔌 SYSTEM PATHS ==========================
# Root of the repository
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Directory for all log files
LOG_DIR = os.path.join(BASE_DIR, "logs")
# Location where generated CSVs are stored
CSV_DIR = os.path.join(BASE_DIR, "output", "csv")
# Duplicate to keep legacy modules working
CSV_OUTPUT_DIR = os.path.join(BASE_DIR, "output", "csv")
# Location for downloaded funded address lists
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
FULL_DIR = os.path.join(DOWNLOADS_DIR, "full")
UNIQUE_DIR = os.path.join(DOWNLOADS_DIR, "unique")
# Where matches and encrypted alerts are archived
MATCHES_DIR = os.path.join(BASE_DIR, "matches")
VANITY_OUTPUT_DIR = os.path.join(BASE_DIR, "vanity_output")
# Local audio clips for alerts
SOUND_CLIPS_DIR = os.path.join(BASE_DIR, "alerts", "sounds")
CHECKPOINT_PATH = os.path.join(LOG_DIR, "restore_checkpoint.json")
# Track which CSVs have been processed
CHECKED_CSV_LOG = os.path.join(LOG_DIR, "checked_csvs.txt")
RECHECKED_CSV_LOG = os.path.join(LOG_DIR, "rechecked_csvs.txt")
# Alias for backward compatibility
DOWNLOAD_DIR = DOWNLOADS_DIR
CHECKPOINT_FILE = os.path.join(BASE_DIR, "checkpoint.json")

# --- VanitySearch Settings ---
VANITY_PATTERN = "1**"  # Change this pattern to match your target (e.g., starts with 1)
VANITYSEARCH_PATH = os.path.join(BASE_DIR, "bin", "vanitysearch.exe")  # Adjust if renamed
MAX_KEYS_PER_FILE = 100_000  #Deprecated
# Output file rotation config (for VanitySearch stream)
MAX_OUTPUT_FILE_SIZE = 250 * 1024 * 1024  # 250 MB default
MAX_OUTPUT_LINES = 200_000              # 200,000 lines per file
USE_GPU = True
ROTATE_INTERVAL_SECONDS = 60

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
ENABLE_PGP = True
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
PGP_PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "sparkles_public_key.asc")

# ===================== 🎧 ALERT SETTINGS ==========================
ALERT_PHRASE = "The Beacons Have Been Lit, Gondor Calls for Aid!"
ENABLE_AUDIO_ALERT_LOCAL = True
ALERT_SOUND_FILE = os.path.join(SOUND_CLIPS_DIR, "gondor_alert.wav")
ENABLE_DESKTOP_WINDOW_ALERT = True
ALERT_POPUP_COLOR_1 = "#FF0000"
ALERT_POPUP_COLOR_2 = "#000000"

# ===================== 🔗 API KEYS ==========================
TOKENVIEW_API_KEY = os.getenv("TOKENVIEW_API_KEY", "")

# ===================== 🌍 COIN SOURCES ==========================
COIN_DOWNLOAD_URLS = {
    "btc": "http://addresses.loyce.club/Bitcoin_addresses_LATEST.txt.gz",
    "doge": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dogecoin/Latest_Dogecoin_Addresses.tsv.gz",
    "ltc": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Litecoin/Latest_Litecoin_Addresses.tsv.gz",
    "eth": "https://raw.githubusercontent.com/Pymmdrza/Rich-Address-Wallet/refs/heads/main/ETHEREUM/EthRich.txt",
    "bch": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/BitcoinCash/Latest_BitcoinCash_Addresses.tsv.gz",
    "dash": "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dash/Latest_Dash_Addresses.tsv.gz"
}
MAX_DAILY_FILES_PER_COIN = 2
FILTER_ONLY_P2PKH = True

# ===================== 🔢 KEYGEN ==========================
USE_GPU = True
USE_CPU_FALLBACK = True
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

# ===================== ALTCOIN ==========================
ALTCOIN_GPUS_INDEX = [2]
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
SHOW_MATCHES_TODAY = True
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


# ===================== 🔒 SECURITY ==========================
DASHBOARD_RESET_PASSWORD = "Mandarin66!"
DELETE_CONFIRMATION_PASSWORD = "Mandarin66!"
DASHBOARD_PASSWORD = DASHBOARD_RESET_PASSWORD  # Alias for UI compatibility

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

# === LOCAL AUDIO ALERT ===
ENABLE_AUDIO_ALERT_LOCAL = True
ALERT_SOUND_FILE = os.path.join(SOUND_CLIPS_DIR, "gondor-calls-for-aid.mp3")  # Must exist or alert will be skipped

# === DESKTOP POP-UP WINDOW ALERT ===
ENABLE_DESKTOP_WINDOW_ALERT = True
ALERT_POPUP_COLOR_1 = "#FF0000"  # First flash color
ALERT_POPUP_COLOR_2 = "#000000"  # Second flash color
ALERT_PHRASE = "The Beacons Have Been Lit, Gondor Calls for Aid!"  # Message shown in window

# === PGP ENCRYPTED MATCH ALERT OUTPUT ===
ENABLE_PGP = True
PGP_PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "Sparkles-allinkeys_0x3A94D30E_public.asc")  # Must be a valid ASCII armored key file

# === EMAIL ALERT CONFIGURATION ===
ALERT_EMAIL_ENABLED = True
ALERT_EMAIL_SENDER = "emailsenderbtc@gmail.com"
ALERT_EMAIL_PASSWORD = "Mandarin66!"
ALERT_EMAIL_RECIPIENTS = ["onqdirector@gmail.com", "reesecobalt@gmail.com"]
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587
INCLUDE_MATCH_INFO = True
ENCRYPTED_MESSAGE = True
# SMTP Credentials (required if ALERT_EMAIL_ENABLED is True)
SMTP_SERVER = "smtp.gmail.com"           # Or use your provider's SMTP host
SMTP_PORT = 587                          # TLS port (use 465 for SSL)
SMTP_USERNAME = "emailsenderbtc@gmail.com"        # Replace with your actual sending email
SMTP_PASSWORD = "btcsender"    # App password if using Gmail 2FA
ALERT_EMAIL_FROM = SMTP_USERNAME  # or hardcode like "you@example.com"
ALERT_EMAIL_TO = ALERT_EMAIL_RECIPIENTS  # DONT CHANGE HERE CHANGE ALERT_EMAIL_RECIPIENTS OPTION ABOVE 


# === TELEGRAM BOT ALERT CONFIGURATION ===
ALERT_TELEGRAM_ENABLED = True
ENABLE_TELEGRAM_ALERT = ALERT_TELEGRAM_ENABLED # alias for backward compatibility dont modify
TELEGRAM_BOT_TOKEN = "6882165186:AAHvqWPCooG5ElvjHWSXZJJFA-ZCbFYD5Ys"
TELEGRAM_CHAT_ID = "t.me/sparklespuzzlebot"

# === SMS VIA TWILIO ===
ALERT_SMS_ENABLED = True
ENABLE_SMS_ALERT = ALERT_SMS_ENABLED # alias for backward compatibility dont modify
TWILIO_SID = "AC9fab6c5e3a541ada1cf4c6b3956bc615"
TWILIO_AUTH_TOKEN = "4aa3b017524e75237752997e658ab16f"
TWILIO_FROM_NUMBER = "+16696666608"
TWILIO_TO_NUMBER = "+16318792320"
TWILIO_TO = TWILIO_TO_NUMBER # Alias do not change
TWILIO_TO_SMS = TWILIO_TO_NUMBER  # alias for backward compatibility
TWILIO_FROM = TWILIO_FROM_NUMBER  # alias for backward compatibility
ENABLE_PHONE_CALL_ALERT = True
TWILIO_CALL_TO_NUMBER = "+16318792320"
TWILIO_TOKEN = TWILIO_AUTH_TOKEN  # Alias do not change
TWILIO_TO_CALL = TWILIO_CALL_TO_NUMBER # Alias do not change

# === DISCORD WEBHOOK ALERTS ===
ALERT_DISCORD_ENABLED = False
ENABLE_DISCORD_ALERT = ALERT_DISCORD_ENABLED # Alias do not change
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."

# === HOME ASSISTANT / IoT WEBHOOK ===
ALERT_HOME_ASSISTANT_ENABLED = False
ENABLE_HOME_ASSISTANT_ALERT = ALERT_HOME_ASSISTANT_ENABLED # Alias do not change
HOME_ASSISTANT_WEBHOOK = "https://your-home-assistant-url/api/webhook/..."
HOME_ASSISTANT_URL = HOME_ASSISTANT_WEBHOOK # Alias do not change
HOME_ASSISTANT_TOKEN = "your home assistant api token here"

# === CLOUD STORAGE MATCH BACKUPS ===

# iCloud
ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE = False
ICLOUD_LOGIN = "you@icloud.com"
ICLOUD_PASSWORD = "yourpassword"
ICLOUD_DRIVE_PATH = "/path/on/icloud"
ENABLE_CLOUD_UPLOAD = ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE # Alias do not change

# Google Drive
ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE = False
GOOGLE_DRIVE_LOGIN = "you@gmail.com"
GOOGLE_DRIVE_PASSWORD = "yourpassword"
GOOGLE_DRIVE_FILE_PATH = "/path/on/gdrive"

# Dropbox
ALERT_SAVE_MATCHES_TO_DROPBOX = False
DROPBOX_LOGIN = "you@protonmail.com"
DROPBOX_PASSWORD = "yourpassword"
DROPBOX_FILE_PATH = "/dropbox/folder"

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
    "csv_checker": True,
    "gpu_stats": True,
    "gpu_assignments": True,
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
    "vanity_progress_percent": "Keygen Progress %",
    "addresses_checked_today": "Addresses Checked Today",
    "addresses_checked_lifetime": "Addresses Checked Lifetime",
    "backlog_files_queued": "Backlog Queue", 
    "backlog_eta": "Backlog ETA",
    "backlog_avg_time": "Avg. Backlog Time",
    "backlog_current_file": "Current Backlog File",
    "csv_checker": "CSV Checker",
    "gpu_stats": "GPU Stats",
    "gpu_assignments": "GPU Assignments",
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

