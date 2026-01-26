from pathlib import Path
import importlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.pop("core.downloader", None)
downloader = importlib.import_module("core.downloader")

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_address_lines_skips_header():
    path = FIXTURES / "btc_addresses_sample.txt"
    with open(path, "r", encoding="utf-8") as f:
        addresses = list(downloader.parse_address_lines(f))
    assert addresses == ["1ABC", "3def", "BC1xyz"]


def test_clean_address_file(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text((FIXTURES / "btc_addresses_sample.txt").read_text(), encoding="utf-8")
    downloader.clean_address_file(sample)
    assert sample.read_text(encoding="utf-8") == "1ABC\n3def\nBC1xyz"


def test_load_btc_funded_multi(tmp_path):
    sample = tmp_path / "btc.txt"
    sample.write_text("1ABC\n3def\nBC1XYZ\n", encoding="utf-8")
    result = downloader.load_btc_funded_multi(sample)
    assert result["p2pkh"] == {"1ABC"}
    assert result["bech32"] == {"bc1xyz"}
    assert result["p2sh"] == set()
    assert result["all"] == {"1ABC", "bc1xyz"}
