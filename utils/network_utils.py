import requests
from urllib.parse import urlparse

from core.logger import get_logger

logger = get_logger(__name__)


def get_with_https_fallback(url: str, **kwargs) -> requests.Response:
    """Retrieve URL, retrying over HTTP if HTTPS fails for loyce.club hosts."""
    try:
        r = requests.get(url, **kwargs)
        r.raise_for_status()
        return r
    except requests.RequestException:
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.netloc.endswith("loyce.club"):
            fallback_url = url.replace("https://", "http://", 1)
            logger.warning(
                f"HTTPS request failed for {url}, falling back to {fallback_url}"
            )
            r = requests.get(fallback_url, **kwargs)
            r.raise_for_status()
            return r
        raise
