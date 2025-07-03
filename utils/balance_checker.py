import requests
from config.settings import TOKENVIEW_API_KEY
from core.logger import log_message

TOKENVIEW_URL = "https://services.tokenview.io/vipapi/addr/{coin}/{address}"

def fetch_live_balance(address, coin):
    """Fetch the current balance for a given address using the TokenView API."""
    url = TOKENVIEW_URL.format(coin=coin, address=address)
    headers = {}
    if TOKENVIEW_API_KEY:
        headers["token"] = TOKENVIEW_API_KEY
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        bal_str = None
        if isinstance(data, dict):
            bal_str = (
                data.get("data", {}).get("balance")
                or data.get("data", {}).get("finalBalance")
            )
        if bal_str is not None:
            try:
                return float(bal_str)
            except (TypeError, ValueError):
                pass
    except Exception as exc:
        log_message(f"⚠️ Failed to fetch balance for {address}: {exc}", "WARN")
    return None
