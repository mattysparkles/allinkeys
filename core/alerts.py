import os
import json
import csv
import time
import smtplib
import requests
import threading
import queue
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
    ALERT_SOUND_FILE, ALERT_POPUP_COLOR_1, ALERT_POPUP_COLOR_2, ALERT_PHRASE,
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


def set_alert_flag(name, value):
    if name in ALERT_FLAGS:
        ALERT_FLAGS[name] = value
        try:
            import config.settings as settings
            if hasattr(settings, 'ALERT_CHECKBOXES'):
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

    # 🖥️ Desktop Window Alert
    if ALERT_FLAGS.get("ENABLE_DESKTOP_WINDOW_ALERT"):
        try:
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

    # 🔊 Sound Alert (queued)
    skip_audio = test_mode or os.path.basename(csv_file) == "test_alerts.csv"
    if ALERT_FLAGS.get("ENABLE_AUDIO_ALERT_LOCAL") and not skip_audio:
        if os.path.exists(ALERT_SOUND_FILE):
            _start_audio_worker()
            audio_queue.put(ALERT_SOUND_FILE)
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
            log_message("📧 Email alert sent.", "INFO")
        except Exception as e:
            log_message(f"❌ Email alert error: {e}", "ERROR")

    # 📲 Telegram Alert
    if ALERT_FLAGS.get("ENABLE_TELEGRAM_ALERT"):
        try:
            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(telegram_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": match_text}, timeout=10)
            if resp.ok and resp.json().get("ok"):
                log_message("📨 Telegram alert sent.", "INFO")
            else:
                log_message(f"❌ Telegram alert failed: {resp.text}", "ERROR")
        except Exception as e:
            log_message(f"❌ Telegram alert error: {e}", "ERROR")

    # 📱 SMS via Twilio
    if ALERT_FLAGS.get("ENABLE_SMS_ALERT") and Client:
        try:
            if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS]):
                raise ValueError("Missing Twilio SMS credentials")
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(body=match_text, from_=TWILIO_FROM, to=TWILIO_TO_SMS)
            log_message("📲 SMS alert sent.", "INFO")
        except Exception as e:
            log_message(f"❌ SMS alert error: {e}", "ERROR")

    # 📞 Phone Call Alert
    if ALERT_FLAGS.get("ENABLE_PHONE_CALL_ALERT") and Client:
        try:
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
            data = {"content": match_text}
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
            headers = {
                "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"message": match_text}
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
        os.makedirs(MATCH_LOG_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime('%Y-%m-%d')
        log_path = os.path.join(MATCH_LOG_DIR, f"matches_{ts}.log")
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(match_data) + "\n")
        log_message("📝 Match written to local log.", "INFO")
    except Exception as e:
        log_message(f"❌ Local match logging error: {e}", "ERROR")


def trigger_startup_alerts():
    """
    Sends startup alerts through configured channels.
    """
    from core.dashboard import set_metric
    if not ENABLE_ALERTS:
        log_message("🚫 Alerts are disabled in config.", "INFO")
        return

    set_metric("status.alerts", True)
    try:
        log_message("📣 Triggering startup alerts...", "INFO")
        # Extend to alert channels if needed
    except Exception as e:
        log_message(f"❌ Failed to trigger startup alerts: {e}", "ERROR")
    finally:
        set_metric("status.alerts", False)


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
