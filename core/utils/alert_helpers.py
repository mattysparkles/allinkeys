import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Union, List

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


_TELEGRAM_KEYS: List[str] | None = None
_TELEGRAM_KEY_IDX: int = 0


def _get_telegram_keys(primary: str) -> List[str]:
    """Return list of Telegram API keys to try, rotating if env provides multiple.

    Uses ``TELEGRAM_API_KEYS`` (comma-separated). If not set, falls back to
    the provided ``primary`` token.
    """
    global _TELEGRAM_KEYS
    if _TELEGRAM_KEYS is None:
        env_keys = os.getenv("TELEGRAM_API_KEYS", "").strip()
        keys = [k.strip() for k in env_keys.split(",") if k.strip()]
        _TELEGRAM_KEYS = keys if keys else [primary]
    return list(_TELEGRAM_KEYS)


def send_telegram_alert(message: str, bot_token: str, chat_id: str, timeout: int = 10) -> bool:
    """Send a Telegram message using the bot API with key rotation on 429/5xx."""
    global _TELEGRAM_KEY_IDX
    keys = _get_telegram_keys(bot_token)
    n = len(keys)
    tried = 0
    last_err = None
    while tried < n:
        token = keys[_TELEGRAM_KEY_IDX % n]
        try:
            telegram_url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(
                telegram_url,
                json={"chat_id": chat_id, "text": message},
                timeout=timeout,
            )
            if resp.ok and resp.json().get("ok"):
                log_message("[ALERT] 📟 Telegram sent", "INFO")
                return True
            status = resp.status_code
            last_err = resp.text
            # Rotate on 429 or server errors
            if status == 429 or 500 <= status < 600:
                _TELEGRAM_KEY_IDX = (_TELEGRAM_KEY_IDX + 1) % n
                tried += 1
                continue
            # Other errors: do not rotate further
            log_message(f"❌ Telegram alert failed: {resp.text}", "ERROR")
            return False
        except Exception as e:
            last_err = str(e)
            # Try next key on network errors
            _TELEGRAM_KEY_IDX = (_TELEGRAM_KEY_IDX + 1) % n
            tried += 1
            continue
    if last_err:
        log_message(f"❌ Telegram alert error: {last_err}", "WARNING")
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
