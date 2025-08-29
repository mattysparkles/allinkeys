import types
import argparse

from utils.puzzle import get_puzzle_info
import main


def test_puzzle_71_range_and_address():
    info = get_puzzle_info(71)
    assert info["start"] == "0000000000000000000000000000000000000000000000400000000000000000"
    assert info["end"] == "00000000000000000000000000000000000000000000007fffffffffffffffff"
    assert info["address"].startswith("1")


def test_handle_puzzle_mode_sets_settings(monkeypatch):
    info = get_puzzle_info(71)
    dummy_settings = types.SimpleNamespace(VANITY_PATTERN="1**")
    monkeypatch.setattr(main, "settings", dummy_settings, raising=False)
    args = argparse.Namespace(puzzle=71, every=False, target=False)
    main.handle_puzzle_mode(args)
    assert getattr(main.settings, "PUZZLE_START") == info["start"]
    assert main.settings.VANITY_PATTERN == info["address"]
    assert args.compressed is True
