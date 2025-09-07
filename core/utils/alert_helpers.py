import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Union

import requests

from core.logger import log_message


def send_email_alert(message: str, from_addr: str, to_addrs: Union[str, list], subject: str,
                     smtp_server: str, smtp_port: int, username: str, password: str,
                     timeout: int = 10) -> bool:
    """Send an email alert using SMTP.

    Returns True on success, False otherwise.
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = from_addr
        msg['To'] = ",".join(to_addrs) if isinstance(to_addrs, (list, tuple)) else to_addrs
        msg['Subject'] = subject
        msg.attach(MIMEText(message, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout)
        server.starttls()
        server.login(username, password)
        server.send_message(msg)
        server.quit()
        log_message("[ALERT] ✉️ Email sent", "INFO")
        return True
    except Exception as e:
        log_message(f"❌ Email alert error: {e}", "WARNING")
        return False


def send_telegram_alert(message: str, bot_token: str, chat_id: str, timeout: int = 10) -> bool:
    """Send a Telegram message using the bot API."""
    try:
        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(telegram_url, json={"chat_id": chat_id, "text": message}, timeout=timeout)
        if resp.ok and resp.json().get("ok"):
            log_message("[ALERT] 📟 Telegram sent", "INFO")
            return True
        log_message(f"❌ Telegram alert failed: {resp.text}", "ERROR")
    except Exception as e:
        log_message(f"❌ Telegram alert error: {e}", "WARNING")
    return False


def send_discord_alert(message: str, webhook_url: str, timeout: int = 10) -> bool:
    """Send an alert to a Discord webhook."""
    try:
        resp = requests.post(webhook_url, json={"content": message}, timeout=timeout)
        if resp.ok:
            log_message("💬 Discord alert sent.", "INFO")
            return True
        log_message(f"❌ Discord alert failed: {resp.text}", "ERROR")
    except Exception as e:
        log_message(f"❌ Discord alert error: {e}", "ERROR")
    return False


def send_home_assistant_alert(message: str, url: str, token: str, timeout: int = 10) -> bool:
    """Send an alert to Home Assistant."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json={"message": message}, timeout=timeout)
        if resp.ok:
            log_message("🏠 Home Assistant alert sent.", "INFO")
            return True
        log_message(f"❌ Home Assistant alert failed: {resp.text}", "ERROR")
    except Exception as e:
        log_message(f"❌ Home Assistant alert error: {e}", "ERROR")
    return False
