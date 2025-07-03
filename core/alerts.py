import os
import json
import time
import smtplib
import requests
from twilio.rest import Client
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
from datetime import datetime

from config.settings import ENABLE_ALERTS
from config.settings import (
    ALERT_SOUND_FILE, ALERT_POPUP_COLOR_1, ALERT_PHRASE,
    ENABLE_DESKTOP_WINDOW_ALERT, ENABLE_AUDIO_ALERT_LOCAL,
    ALERT_EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    ALERT_EMAIL_TO, ALERT_EMAIL_FROM,
    ENABLE_TELEGRAM_ALERT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    ENABLE_SMS_ALERT, ENABLE_PHONE_CALL_ALERT, TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO_SMS, TWILIO_TO_CALL,
    ENABLE_DISCORD_ALERT, DISCORD_WEBHOOK_URL,
    ENABLE_HOME_ASSISTANT_ALERT, HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN,
    ENABLE_CLOUD_UPLOAD, PGP_PUBLIC_KEY_PATH, MATCH_LOG_DIR
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


def set_alert_flag(name, value):
    if name in ALERT_FLAGS:
        ALERT_FLAGS[name] = value


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

    # 🖥️ Desktop Window Alert
    if ALERT_FLAGS.get("ENABLE_DESKTOP_WINDOW_ALERT"):
        try:
            import tkinter as tk
            win = tk.Tk()
            win.title(alert_type)
            win.geometry("500x150")
            win.configure(bg=ALERT_POPUP_COLOR_1)
            tk.Label(win, text=ALERT_PHRASE, font=("Courier", 16), fg="white", bg=ALERT_POPUP_COLOR_1).pack(expand=True)
            win.after(8000, lambda: win.destroy())
            win.mainloop()
        except Exception as e:
            print(f"Window alert error: {e}")

    # 🔊 Sound Alert
    if ALERT_FLAGS.get("ENABLE_AUDIO_ALERT_LOCAL"):
        try:
            import playsound
            playsound.playsound(ALERT_SOUND_FILE)
        except Exception as e:
            print(f"Sound alert error: {e}")

    # 📧 Email Alert
    if ALERT_FLAGS.get("ALERT_EMAIL_ENABLED"):
        try:
            msg = MIMEMultipart()
            msg['From'] = ALERT_EMAIL_FROM
            msg['To'] = ALERT_EMAIL_TO
            msg['Subject'] = f"AllInKeys {alert_type}"
            msg.attach(MIMEText(match_text, 'plain'))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            print(f"Email alert error: {e}")

    # 📲 Telegram Alert
    if ALERT_FLAGS.get("ENABLE_TELEGRAM_ALERT"):
        try:
            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(telegram_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": match_text})
        except Exception as e:
            print(f"Telegram alert error: {e}")

    # 📱 SMS via Twilio
    if ALERT_FLAGS.get("ENABLE_SMS_ALERT"):
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(body=match_text, from_=TWILIO_FROM, to=TWILIO_TO_SMS)
        except Exception as e:
            print(f"SMS alert error: {e}")

    # 📞 Phone Call Alert
    if ALERT_FLAGS.get("ENABLE_PHONE_CALL_ALERT"):
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.calls.create(
                url='http://demo.twilio.com/docs/voice.xml',
                from_=TWILIO_FROM,
                to=TWILIO_TO_CALL
            )
        except Exception as e:
            print(f"Phone call error: {e}")

    # 💬 Discord Alert
    if ALERT_FLAGS.get("ENABLE_DISCORD_ALERT"):
        try:
            data = {"content": match_text}
            requests.post(DISCORD_WEBHOOK_URL, json=data)
        except Exception as e:
            print(f"Discord alert error: {e}")

    # 🏠 Home Assistant Alert
    if ALERT_FLAGS.get("ENABLE_HOME_ASSISTANT_ALERT"):
        try:
            headers = {
                "Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {"message": match_text}
            requests.post(HOME_ASSISTANT_URL, headers=headers, json=payload)
        except Exception as e:
            print(f"Home Assistant alert error: {e}")

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
        except Exception as e:
            print(f"PGP/cloud upload error: {e}")

    # 📜 Local match log
    try:
        log_path = os.path.join(MATCH_LOG_DIR, "matches.log")
        with open(log_path, 'a') as f:
            f.write(f"{match_text}\n\n")
    except Exception as e:
        print(f"Local match logging error: {e}")


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


def trigger_test_alerts():
    """Fire a test alert using sample data."""
    test_payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "coin": "BTC",
        "address": "1KFHE7w8BhaENAswwryaoccDb6qcT6DbYY",
        "csv_file": "test_alerts.csv",
        "privkey": "TESTKEY"
    }
    alert_match(test_payload, test_mode=True)
