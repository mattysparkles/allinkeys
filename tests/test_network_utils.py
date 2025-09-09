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
