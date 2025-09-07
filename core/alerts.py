import os
import json
import csv
csv.field_size_limit(2**30)  # allow very large CSV fields
import time
import smtplib
import requests
import threading
import queue
import subprocess
import traceback
try:
    from twilio.rest import Client
except Exception:  # handle missing twilio dependency
    Client = None
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
from datetime import datetime

from config.settings import ENABLE_ALERTS, DOWNLOADS_DIR, REDACT_SENSITIVE_DATA_IN_ALERTS
from config.coin_definitions import coin_columns
from config.settings import (
    ALERT_SOUND_FILE, ALERT_POPUP_COLOR_1, ALERT_POPUP_COLOR_2, ALERT_PHRASE,
    ENABLE_DESKTOP_WINDOW_ALERT, ENABLE_AUDIO_ALERT_LOCAL,
    ALERT_EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    ALERT_EMAIL_TO, ALERT_EMAIL_FROM,
    ENABLE_TELEGRAM_ALERT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    ENABLE_SMS_ALERT, ENABLE_PHONE_CALL_ALERT, TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS, TWILIO_TO_CALL,
    ENABLE_DISCORD_ALERT, DISCORD_WEBHOOK_URL,
    ENABLE_HOME_ASSISTANT_ALERT, HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN,
    ENABLE_CLOUD_UPLOAD, PGP_PUBLIC_KEY_PATH, MATCH_LOG_DIR, ENABLE_PGP,
    ENABLE_PGP_ENCRYPTION, PGP_RECIPIENT, PGP_KEYRING_PATH
)

from core.logger import log_message
from core.dashboard import get_metric
from core.worker_bootstrap import _safe_set_metric, _safe_inc_metric

# runtime alert flags that can be toggled from the GUI
ALERT_FLAGS = {
    "ENABLE_AUDIO_ALERT_LOCAL": ENABLE_AUDIO_ALERT_LOCAL,
    "ENABLE_DESKTOP_WINDOW_ALERT": ENABLE_DESKTOP_WINDOW_ALERT,
    "ENABLE_PGP": ENABLE_PGP,
    "ALERT_EMAIL_ENABLED": ALERT_EMAIL_ENABLED,
    "ENABLE_TELEGRAM_ALERT": ENABLE_TELEGRAM_ALERT,
    "ENABLE_SMS_ALERT": ENABLE_SMS_ALERT,
    "ENABLE_PHONE_CALL_ALERT": ENABLE_PHONE_CALL_ALERT,
    "ENABLE_DISCORD_ALERT": ENABLE_DISCORD_ALERT,
    "ENABLE_HOME_ASSISTANT_ALERT": ENABLE_HOME_ASSISTANT_ALERT,
    "ENABLE_CLOUD_UPLOAD": ENABLE_CLOUD_UPLOAD,
}

FLAG_LABELS = {
    "ENABLE_AUDIO_ALERT_LOCAL": "audio",
    "ENABLE_DESKTOP_WINDOW_ALERT": "popup",
    "ENABLE_PGP": "pgp",
    "ALERT_EMAIL_ENABLED": "email",
    "ENABLE_TELEGRAM_ALERT": "telegram",
    "ENABLE_SMS_ALERT": "sms",
    "ENABLE_PHONE_CALL_ALERT": "phone",
    "ENABLE_DISCORD_ALERT": "discord",
    "ENABLE_HOME_ASSISTANT_ALERT": "home_assistant",
    "ENABLE_CLOUD_UPLOAD": "cloud",
}

# Mapping of alert channels for metrics tracking
ALERT_CHANNELS = [
    "audio",
    "email",
    "telegram",
    "popup",
    "sms",
    "file",
    "cloud",
    "phone",
    "discord",
    "webhook",
    "home_assistant",
]

# Queue for sequential audio alerts
audio_queue = queue.Queue()
audio_thread = None


def _audio_worker():
    """Background worker that plays alert sounds sequentially."""
    from playsound import playsound  # imported here to avoid startup cost
    while True:
        sound = audio_queue.get()
        if sound is None:
            break
        try:
            playsound(sound)
            log_message("🔔 Played alert sound.")
        except Exception as exc:
            log_message(f"❌ Audio alert error: {exc}", "ERROR")


def _start_audio_worker():
    global audio_thread
    if audio_thread is None or not audio_thread.is_alive():
        audio_thread = threading.Thread(target=_audio_worker, daemon=True)
        audio_thread.start()


def _redact_sensitive_fields(data: dict) -> dict:
    """Return a copy of ``data`` with seeds/private keys redacted."""
    if not REDACT_SENSITIVE_DATA_IN_ALERTS:
        return data
    redacted = {}
    for key, value in data.items():
        if any(token in key.lower() for token in ("priv", "seed", "mnemonic", "wif")) and str(value).upper() not in {"N/A", "TEST"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _log_alert_consent():
    """Log which alert channels are enabled at startup."""
    for flag, enabled in ALERT_FLAGS.items():
        channel = FLAG_LABELS.get(flag, flag)
        status = "ENABLED" if enabled else "disabled"
        log_message(f"Consent for {channel} alerts: {status}", "INFO")


def _show_desktop_popup(alert_type: str):
    """Display the desktop popup without blocking other alerts."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.title(alert_type)
        win.configure(bg=ALERT_POPUP_COLOR_1)
        win.geometry("600x250")
        lbl = tk.Label(
            win,
            text=ALERT_PHRASE,
            fg="white",
            bg=ALERT_POPUP_COLOR_1,
            font=("Helvetica", 16, "bold"),
            wraplength=560,
            justify="center",
        )
        lbl.pack(expand=True, fill="both", padx=10, pady=10)

        def flash():
            new = ALERT_POPUP_COLOR_2 if win["bg"] == ALERT_POPUP_COLOR_1 else ALERT_POPUP_COLOR_1
            win.configure(bg=new)
            lbl.configure(bg=new)
            win.after(500, flash)

        flash()
        root.mainloop()
    except Exception as exc:
        log_message(f"❌ Desktop alert error: {exc}", "ERROR")


# ------------------------- PGP SUPPORT -------------------------
_pgp_ok = False


def init_pgp():
    """Validate that a usable PGP key is available."""
    global _pgp_ok
    if not (ENABLE_PGP_ENCRYPTION and PGP_RECIPIENT):
        log_message(
            "PGP encryption disabled or recipient not set.",
            "INFO",
        )
        return
    cmd = ["gpg", "--list-keys", PGP_RECIPIENT]
    if PGP_KEYRING_PATH:
        cmd = ["gpg", "--keyring", PGP_KEYRING_PATH, "--list-keys", PGP_RECIPIENT]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or PGP_RECIPIENT not in res.stdout:
        log_message(
            "❌ PGP recipient key not found. To import a public key:\n  gpg --import publickey.asc\n  gpg --list-keys\nEnsure PGP_RECIPIENT matches the uid/email shown by --list-keys.",
            "ERROR",
        )
        _pgp_ok = False
        return
    _pgp_ok = True
    log_message(f"🔐 PGP encryption active for {PGP_RECIPIENT}", "INFO")


def pgp_encrypt(text: str):
    if not _pgp_ok:
        return None
    cmd = ["gpg", "--armor", "--encrypt", "-r", PGP_RECIPIENT]
    if PGP_KEYRING_PATH:
        cmd = ["gpg", "--keyring", PGP_KEYRING_PATH, "--armor", "--encrypt", "-r", PGP_RECIPIENT]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(text)
    if proc.returncode != 0:
        log_message(f"❌ PGP encryption failed: {err}", "ERROR")
        return None
    return out


init_pgp()


def send_phone_call_alert(message: str):
    """Send a Twilio phone call if enabled."""
    if not (ALERT_FLAGS.get("ENABLE_PHONE_CALL_ALERT") and Client):
        return
    try:
        if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_CALL]):
            raise ValueError("Missing Twilio call credentials")
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.calls.create(
            twiml=f'<Response><Say>{message}</Say></Response>',
            from_=TWILIO_FROM,
            to=TWILIO_TO_CALL,
        )
        log_message("📞 Phone call alert triggered.", "INFO")
        _safe_inc_metric("alerts_sent_today.phone")
        _safe_inc_metric("alerts_sent_lifetime.phone")
    except Exception as exc:
        log_message(f"❌ Phone call error: {exc}\n{traceback.format_exc()}", "ERROR")


def set_alert_flag(name, value):
    """Update runtime alert flags and reflect changes in settings."""
    ALERT_FLAGS[name] = value
    try:
        import config.settings as settings
        setattr(settings, name, value)
        if hasattr(settings, "ALERT_CHECKBOXES"):
            settings.ALERT_CHECKBOXES[name] = value
    except Exception:
        pass


def alert_match(match_data, test_mode=False):
    """
    Sends alerts through all enabled channels.
    Accepts either:
        - A dict with match details (coin, address, timestamp, etc.)
        - A dict with {"encrypted": "<PGP-encoded string>"} for PGP/cloud upload only
    """
    if not isinstance(match_data, dict):
        log_message("❌ Malformed alert_match call — expected dict.", "ERROR")
        return

    if not ENABLE_ALERTS:
        log_message("🚫 Alerts are disabled in config.", "INFO")
        return

    if get_metric("alerts_sent_today") is None:
        _safe_set_metric("alerts_sent_today", {c: 0 for c in ALERT_CHANNELS})

    # Handle PGP-only encrypted blob
    if "encrypted" in match_data:
        try:
            timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"encrypted_match_{timestamp}.pgp"
            full_path = os.path.join(MATCH_LOG_DIR, filename)
            with open(full_path, "w") as f:
                f.write(match_data["encrypted"])
            log_message(f"☁ Encrypted match stored to: {filename}", "INFO")
        except Exception as e:
            log_message(f"❌ Failed to store encrypted match: {e}", "ERROR")
        return

    safe_data = _redact_sensitive_fields(match_data)
    timestamp = safe_data.get("timestamp") or time.strftime('%Y-%m-%d %H:%M:%S')
    coin = safe_data.get("coin", "BTC")
    address = safe_data.get("address", safe_data.get("btc_U", "unknown"))
    csv_file = safe_data.get("csv_file", "unknown")
    privkey_display = safe_data.get("privkey", "N/A")
    alert_type = "TEST MATCH" if test_mode else "MATCH FOUND"

    # Plain text may include sensitive data for optional encryption
    plain_match_text = (
        f"[{timestamp}] {alert_type}!\n"
        f"Coin: {coin}\nAddress: {address}\nCSV: {csv_file}\nWIF: {match_data.get('privkey', 'N/A')}"
    )
    match_text = (
        f"[{timestamp}] {alert_type}!\n"
        f"Coin: {coin}\nAddress: {address}\nCSV: {csv_file}\nWIF: {privkey_display}"
    )

    log_message(
        f"🎯 Match found: {json.dumps(safe_data if REDACT_SENSITIVE_DATA_IN_ALERTS else match_data)}",
        "INFO",
    )
    log_message(f"🚨 {alert_type}: {address} (File: {csv_file})")
    encrypted_blob = pgp_encrypt(plain_match_text)
    if encrypted_blob:
        try:
            ts = time.strftime('%Y-%m-%d_%H-%M-%S')
            fname = os.path.join(MATCH_LOG_DIR, f"encrypted_match_{ts}.pgp")
            with open(fname, "w") as ef:
                ef.write(encrypted_blob)
            log_message(f"☁ Encrypted match stored to: {os.path.basename(fname)}", "INFO")
        except Exception as exc:
            log_message(f"❌ Failed to store encrypted match: {exc}", "ERROR")

    # 🖥️ Desktop Window Alert
    if ALERT_FLAGS.get("ENABLE_DESKTOP_WINDOW_ALERT"):
        try:
            threading.Thread(target=_show_desktop_popup, args=(alert_type,), daemon=True).start()
            log_message("✅ Desktop popup displayed.", "INFO")
            _safe_inc_metric("alerts_sent_today.popup")
            _safe_inc_metric("alerts_sent_lifetime.popup")
        except Exception as e:
            log_message(f"❌ Desktop alert error: {e}", "ERROR")

    # 🔊 Sound Alert (queued)
    skip_audio = test_mode or os.path.basename(csv_file) == "test_alerts.csv"
    if ALERT_FLAGS.get("ENABLE_AUDIO_ALERT_LOCAL") and not skip_audio:
        if os.path.exists(ALERT_SOUND_FILE):
            _start_audio_worker()
            audio_queue.put(ALERT_SOUND_FILE)
            _safe_inc_metric("alerts_sent_today.audio")
            _safe_inc_metric("alerts_sent_lifetime.audio")
        else:
            log_message(f"❌ Sound file not found: {ALERT_SOUND_FILE}", "ERROR")

    # 📧 Email Alert
    if ALERT_FLAGS.get("ALERT_EMAIL_ENABLED"):
        try:
            msg = MIMEMultipart()
            msg['From'] = ALERT_EMAIL_FROM
            msg['To'] = ",".join(ALERT_EMAIL_TO) if isinstance(ALERT_EMAIL_TO, list) else ALERT_EMAIL_TO
            msg['Subject'] = f"AllInKeys {alert_type}"
            msg.attach(MIMEText(match_text, 'plain'))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            log_message("[ALERT] ✉️ Email sent", "INFO")
            _safe_inc_metric("alerts_sent_today.email")
            _safe_inc_metric("alerts_sent_lifetime.email")
        except Exception as e:
            log_message(f"❌ Email alert error: {e}", "WARNING")

    # 📲 Telegram Alert
    if ALERT_FLAGS.get("ENABLE_TELEGRAM_ALERT"):
        try:
            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(telegram_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": match_text}, timeout=10)
            if resp.ok and resp.json().get("ok"):
                log_message("[ALERT] 📟 Telegram sent", "INFO")
                _safe_inc_metric("alerts_sent_today.telegram")
                _safe_inc_metric("alerts_sent_lifetime.telegram")
            else:
                log_message(f"❌ Telegram alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Telegram alert error: {e}", "WARNING")

    # 📱 SMS via Twilio
    if ALERT_FLAGS.get("ENABLE_SMS_ALERT") and Client:
        try:
            if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS]):
                raise ValueError("Missing Twilio SMS credentials")
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(body=match_text, from_=TWILIO_FROM, to=TWILIO_TO_SMS)
            log_message("📲 SMS alert sent.", "INFO")
            _safe_inc_metric("alerts_sent_today.sms")
            _safe_inc_metric("alerts_sent_lifetime.sms")
        except Exception as e:
            log_message(f"❌ SMS alert error: {e}", "WARNING")

    send_phone_call_alert(match_text)

    # 💬 Discord Alert
    if ALERT_FLAGS.get("ENABLE_DISCORD_ALERT"):
        try:
            data = {"content": match_text}
            resp = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
            if resp.ok:
                log_message("💬 Discord alert sent.", "INFO")
                _safe_inc_metric("alerts_sent_today.discord")
                _safe_inc_metric("alerts_sent_lifetime.discord")
            else:
                log_message(f"❌ Discord alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Discord alert error: {e}", "ERROR")

    # 🏠 Home Assistant Alert
    if ALERT_FLAGS.get("ENABLE_HOME_ASSISTANT_ALERT"):
        try:
            headers = {
                "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"message": match_text}
            resp = requests.post(HOME_ASSISTANT_URL, headers=headers, json=payload, timeout=10)
            if resp.ok:
                log_message("🏠 Home Assistant alert sent.", "INFO")
                _safe_inc_metric("alerts_sent_today.home_assistant")
                _safe_inc_metric("alerts_sent_lifetime.home_assistant")
            else:
                log_message(f"❌ Home Assistant alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Home Assistant alert error: {e}", "ERROR")

    # ☁ PGP + Cloud Upload
    if ALERT_FLAGS.get("ENABLE_CLOUD_UPLOAD"):
        try:
            with open(PGP_PUBLIC_KEY_PATH, "rb") as pubkey_file:
                pubkey = RSA.import_key(pubkey_file.read())
            cipher = PKCS1_OAEP.new(pubkey)
            encrypted = cipher.encrypt(json.dumps(match_data).encode("utf-8"))
            b64_encrypted = base64.b64encode(encrypted).decode()
            timestamp_filename = f"{coin}_match_{timestamp.replace(':', '-')}.pgp"
            full_path = os.path.join(MATCH_LOG_DIR, timestamp_filename)
            with open(full_path, 'w') as f:
                f.write(b64_encrypted)
            log_message("☁ Encrypted match uploaded locally.", "INFO")
            _safe_inc_metric("alerts_sent_today.cloud")
            _safe_inc_metric("alerts_sent_lifetime.cloud")
        except Exception as e:
            log_message(f"❌ PGP/cloud upload error: {e}", "ERROR")

    # 📜 Local match log
    try:
        os.makedirs(MATCH_LOG_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime('%Y-%m-%d')
        log_path = os.path.join(MATCH_LOG_DIR, f"matches_{ts}.log")
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(safe_data) + "\n")
        log_message("📝 Match written to local log.", "INFO")
        _safe_inc_metric("alerts_sent_today.file")
        _safe_inc_metric("alerts_sent_lifetime.file")
    except Exception as e:
        log_message(f"❌ Local match logging error: {e}", "ERROR")


def trigger_startup_alerts(shared_metrics=None):
    """
    Sends startup alerts through configured channels.
    """
    from core.worker_bootstrap import ensure_metrics_ready
    try:
        ensure_metrics_ready(shared_metrics)
    except Exception:
        pass
    if not ENABLE_ALERTS:
        log_message("🚫 Alerts are disabled in config.", "INFO")
        return

    _log_alert_consent()

    # Ensure dashboard reflects that alerts are active on startup
    _safe_set_metric("status.alerts", "Running")
    _safe_set_metric("alerts_status", "Running")
    try:
        log_message("📣 Triggering startup alerts...", "INFO")
        # Extend to alert channels if needed
    except Exception as e:
        log_message(f"❌ Failed to trigger startup alerts: {e}", "ERROR")


def run_test_alerts_from_csv(csv_path=None):
    """Send test alerts for each address in the CSV file."""
    if csv_path is None:
        csv_path = os.path.join(DOWNLOADS_DIR, "test_alerts.csv")

    if not os.path.exists(csv_path):
        from core.downloader import generate_test_csv
        csv_path = generate_test_csv()
        if not csv_path or not os.path.exists(csv_path):
            log_message("⚠️ test_alerts.csv not found and could not be generated.", "WARN")
            return

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, 1):
            try:
                for coin, columns in coin_columns.items():
                    for col in columns:
                        addr = row.get(col, "").strip()
                        if not addr:
                            continue
                        payload = {
                            "timestamp": datetime.utcnow().isoformat(),
                            "coin": coin,
                            "address": addr,
                            "csv_file": os.path.basename(csv_path),
                            "privkey": row.get("private_key", "TEST")
                        }
                        alert_match(payload, test_mode=True)
                        log_message(f"✅ Test alert sent for {addr}", "INFO")
            except Exception as exc:
                log_message(f"❌ Failed sending test alert row {row_num}: {exc}", "ERROR")


# Backwards compatibility
def trigger_test_alerts():
    run_test_alerts_from_csv()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    if args.test:
        run_test_alerts_from_csv()
