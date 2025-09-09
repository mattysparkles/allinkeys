from pathlib import Path
import importlib
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.pop("utils.network_utils", None)
network_utils = importlib.import_module("utils.network_utils")


def test_download_file_requires_https(tmp_path):
    dest = tmp_path / "out.txt"
    with pytest.raises(ValueError, match="HTTPS is required"):
        network_utils.download_file("http://example.com/data.txt", str(dest))


def test_download_file_skips_placeholder_sha256(tmp_path, monkeypatch):
    dest = tmp_path / "out.txt"
    content = b"hello world"

    class DummyResponse:
        def __init__(self):
            self.headers = {"Content-Length": str(len(content))}

        def iter_content(self, chunk_size=8192):
            yield content

        def raise_for_status(self):
            pass

    def fake_get(url, stream=True, **kwargs):
        return DummyResponse()

    monkeypatch.setattr(network_utils.requests, "get", fake_get)
    network_utils.download_file(
        "https://example.com/data.txt", str(dest), expected_sha256="0" * 64
    )

    assert dest.read_bytes() == content
