import os
import hashlib
import requests  # type: ignore[import-untyped]
from urllib.parse import urlparse

from core.logger import log_message


def download_file(
    url: str,
    dest_path: str,
    *,
    expected_sha256: str | None = None,
    chunk_size: int = 8192,
    progress_cb=None,
    **kwargs,
) -> None:
    """Download ``url`` to ``dest_path`` enforcing HTTPS and optional SHA256 verification.

    If ``expected_sha256`` is provided and does not match the computed digest the
    file is deleted and a security alert is logged before raising ``ValueError``.
    ``progress_cb`` is called with ``(downloaded_bytes, total_bytes)`` after each
    chunk allowing callers to track progress.
    """

    parsed = urlparse(url)
    if parsed.scheme != "https":
        log_message(f"Blocked insecure URL: {url}", "ALERT")
        raise ValueError("HTTPS is required for all downloads")

    r = requests.get(url, stream=True, **kwargs)
    r.raise_for_status()

    total = int(r.headers.get("Content-Length", 0))
    downloaded = 0
    hasher = hashlib.sha256()

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            f.write(chunk)
            hasher.update(chunk)
            downloaded += len(chunk)
            if progress_cb:
                progress_cb(downloaded, total)

    digest = hasher.hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        log_message(
            f"SHA256 mismatch for {url}: expected {expected_sha256} got {digest}",
            "ALERT",
        )
        try:
            os.remove(dest_path)
        finally:
            pass
        raise ValueError("SHA256 mismatch")


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
            log_message(
                f"HTTPS request failed for {url}, falling back to {fallback_url}",
                "WARN",
            )
            r = requests.get(fallback_url, **kwargs)
            r.raise_for_status()
            return r
        raise
