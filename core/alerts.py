import os
import json
import csv
import time
import smtplib
import requests
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

from config.settings import ENABLE_ALERTS, DOWNLOADS_DIR
from config.coin_definitions import coin_columns
from config.settings import (
    ALERT_SOUND_FILE, ALERT_POPUP_COLOR_1, ALERT_PHRASE,
    ENABLE_DESKTOP_WINDOW_ALERT, ENABLE_AUDIO_ALERT_LOCAL,
    ALERT_EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    ALERT_EMAIL_TO, ALERT_EMAIL_FROM,
    ENABLE_TELEGRAM_ALERT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    ENABLE_SMS_ALERT, ENABLE_PHONE_CALL_ALERT, TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS, TWILIO_TO_CALL,
    ENABLE_DISCORD_ALERT, DISCORD_WEBHOOK_URL,
    ENABLE_HOME_ASSISTANT_ALERT, HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN,
    ENABLE_CLOUD_UPLOAD, PGP_PUBLIC_KEY_PATH, MATCH_LOG_DIR, ENABLE_PGP
)

from core.logger import log_message
from utils.pgp_utils import encrypt_with_pgp

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

# Mapping of checkbox option names from ``settings.ALERT_CHECKBOXES`` to the
# internal ``ALERT_FLAGS`` keys. This keeps the GUI toggles working even if the
# naming differs between modules.
ALERT_FLAG_ALIASES = {
    "ALERT_TELEGRAM_ENABLED": "ENABLE_TELEGRAM_ALERT",
    "ALERT_SMS_ENABLED": "ENABLE_SMS_ALERT",
    "ALERT_DISCORD_ENABLED": "ENABLE_DISCORD_ALERT",
    "ALERT_HOME_ASSISTANT_ENABLED": "ENABLE_HOME_ASSISTANT_ALERT",
    # Various cloud storage options map to the generic cloud upload flag
    "ALERT_SAVE_MATCHES_TO_ICLOUD_DRIVE": "ENABLE_CLOUD_UPLOAD",
    "ALERT_SAVE_MATCHES_TO_GOOGLE_DRIVE": "ENABLE_CLOUD_UPLOAD",
    "ALERT_SAVE_MATCHES_TO_DROPBOX": "ENABLE_CLOUD_UPLOAD",
    "ALERT_SAVE_MATCHES_TO_LOCAL_FILE": "ENABLE_CLOUD_UPLOAD",
}


def set_alert_flag(name, value):
    """Update runtime alert flags from the GUI."""
    key = ALERT_FLAG_ALIASES.get(name, name)
    if key in ALERT_FLAGS:
        ALERT_FLAGS[key] = value


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

    timestamp = match_data.get("timestamp") or time.strftime('%Y-%m-%d %H:%M:%S')
    coin = match_data.get("coin", "BTC")
    address = match_data.get("address", match_data.get("btc_U", "unknown"))
    csv_file = match_data.get("csv_file", "unknown")
    privkey = match_data.get("privkey", "N/A")
    alert_type = "TEST MATCH" if test_mode else "MATCH FOUND"

    match_text = f"[{timestamp}] {alert_type}!\nCoin: {coin}\nAddress: {address}\nCSV: {csv_file}\nWIF: {privkey}"
    log_message(f"🚨 {alert_type}: {address} (File: {csv_file})")

    # If PGP is enabled encrypt the payload for any text-based alerts. Channels
    # like popup/audio still display the plaintext match text.
    encrypted_text = None
    if ALERT_FLAGS.get("ENABLE_PGP"):
        try:
            encrypted_text = encrypt_with_pgp(match_text, PGP_PUBLIC_KEY_PATH)
            log_message("🔐 Match data encrypted with PGP.", "DEBUG")
        except Exception as e:
            log_message(f"❌ PGP encryption failed: {e}", "ERROR")

    # 🖥️ Desktop Window Alert
    if ALERT_FLAGS.get("ENABLE_DESKTOP_WINDOW_ALERT"):
        try:
            log_message("[Alert] Desktop popup", "DEBUG")
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            win = tk.Toplevel(root)
            win.title(alert_type)
            win.configure(bg=ALERT_POPUP_COLOR_1)
            win.geometry("520x180")
            lbl = tk.Label(win, text=ALERT_PHRASE, fg="white", bg=ALERT_POPUP_COLOR_1,
                            font=("Helvetica", 18, "bold"))
            lbl.pack(expand=True, fill="both", padx=10, pady=10)

            def flash():
                new = ALERT_POPUP_COLOR_2 if win["bg"] == ALERT_POPUP_COLOR_1 else ALERT_POPUP_COLOR_1
                win.configure(bg=new)
                lbl.configure(bg=new)
                win.after(500, flash)

            flash()
            win.after(8000, root.destroy)
            root.mainloop()
            log_message("✅ Desktop popup displayed.", "INFO")
        except Exception as e:
            log_message(f"❌ Desktop alert error: {e}", "ERROR")

    # 🔊 Sound Alert
    if ALERT_FLAGS.get("ENABLE_AUDIO_ALERT_LOCAL"):
        try:
            log_message("[Alert] Audio", "DEBUG")
            from playsound import playsound
            if os.path.exists(ALERT_SOUND_FILE):
                playsound(ALERT_SOUND_FILE)
                log_message("🔔 Played alert sound.", "INFO")
            else:
                log_message(f"❌ Sound file not found: {ALERT_SOUND_FILE}", "ERROR")
        except Exception as e:
            log_message(f"❌ Audio alert error: {e}", "ERROR")

    # 📧 Email Alert
    if ALERT_FLAGS.get("ALERT_EMAIL_ENABLED"):
        try:
            log_message("[Alert] Email", "DEBUG")
            msg = MIMEMultipart()
            msg['From'] = ALERT_EMAIL_FROM
            msg['To'] = ",".join(ALERT_EMAIL_TO) if isinstance(ALERT_EMAIL_TO, list) else ALERT_EMAIL_TO
            msg['Subject'] = f"AllInKeys {alert_type}"
            msg.attach(MIMEText(encrypted_text or match_text, 'plain'))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            log_message("📧 Email alert sent.", "INFO")
        except Exception as e:
            log_message(f"❌ Email alert error: {e}", "ERROR")

    # 📲 Telegram Alert
    if ALERT_FLAGS.get("ENABLE_TELEGRAM_ALERT"):
        try:
            log_message("[Alert] Telegram", "DEBUG")
            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                telegram_url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": encrypted_text or match_text},
                timeout=10,
            )
            if resp.ok and resp.json().get("ok"):
                log_message("📨 Telegram alert sent.", "INFO")
            else:
                log_message(f"❌ Telegram alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Telegram alert error: {e}", "ERROR")

    # 📱 SMS via Twilio
    if ALERT_FLAGS.get("ENABLE_SMS_ALERT") and Client:
        try:
            log_message("[Alert] SMS", "DEBUG")
            if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS]):
                raise ValueError("Missing Twilio SMS credentials")
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(body=encrypted_text or match_text, from_=TWILIO_FROM, to=TWILIO_TO_SMS)
            log_message("📲 SMS alert sent.", "INFO")
        except Exception as e:
            log_message(f"❌ SMS alert error: {e}", "ERROR")

    # 📞 Phone Call Alert
    if ALERT_FLAGS.get("ENABLE_PHONE_CALL_ALERT") and Client:
        try:
            log_message("[Alert] Phone Call", "DEBUG")
            if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_CALL]):
                raise ValueError("Missing Twilio call credentials")
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.calls.create(
                url='http://demo.twilio.com/docs/voice.xml',
                from_=TWILIO_FROM,
                to=TWILIO_TO_CALL
            )
            log_message("📞 Phone call alert triggered.", "INFO")
        except Exception as e:
            log_message(f"❌ Phone call error: {e}", "ERROR")

    # 💬 Discord Alert
    if ALERT_FLAGS.get("ENABLE_DISCORD_ALERT"):
        try:
            log_message("[Alert] Discord", "DEBUG")
            data = {"content": encrypted_text or match_text}
            resp = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
            if resp.ok:
                log_message("💬 Discord alert sent.", "INFO")
            else:
                log_message(f"❌ Discord alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Discord alert error: {e}", "ERROR")

    # 🏠 Home Assistant Alert
    if ALERT_FLAGS.get("ENABLE_HOME_ASSISTANT_ALERT"):
        try:
            log_message("[Alert] Home Assistant", "DEBUG")
            headers = {
                "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"message": encrypted_text or match_text}
            resp = requests.post(HOME_ASSISTANT_URL, headers=headers, json=payload, timeout=10)
            if resp.ok:
                log_message("🏠 Home Assistant alert sent.", "INFO")
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
        except Exception as e:
            log_message(f"❌ PGP/cloud upload error: {e}", "ERROR")

    # 📜 Local match log
    try:
        log_path = os.path.join(MATCH_LOG_DIR, "matches.log")
        with open(log_path, 'a') as f:
            f.write(f"{match_text}\n\n")
        log_message("📝 Match written to local log.", "INFO")
    except Exception as e:
        log_message(f"❌ Local match logging error: {e}", "ERROR")


def trigger_startup_alerts():
    """
    Sends startup alerts through configured channels.
    """
    if not ENABLE_ALERTS:
        log_message("🚫 Alerts are disabled in config.", "INFO")
        return

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
