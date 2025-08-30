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


def test_generate_random_seed_respects_puzzle(monkeypatch):
    info = get_puzzle_info(5)
    import importlib, sys

    # Tests may stub out ``core.keygen`` in ``sys.modules``; ensure we import the
    # actual module with minimal stubs for its heavy dependencies.
    sys.modules.pop("core.keygen", None)

    mod = types.ModuleType("core.gpu_selector")
    mod.get_vanitysearch_gpu_ids = lambda: []
    sys.modules["core.gpu_selector"] = mod

    mod = types.ModuleType("core.checkpoint")
    mod.load_keygen_checkpoint = lambda *a, **k: None
    mod.save_keygen_checkpoint = lambda *a, **k: None
    sys.modules["core.checkpoint"] = mod

    mod = types.ModuleType("core.logger")
    mod.get_logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda *a, **k: None,
        error=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    sys.modules["core.logger"] = mod

    mod = types.ModuleType("core.dashboard")
    mod.init_shared_metrics = lambda *a, **k: None
    mod.set_metric = lambda *a, **k: None
    mod.increment_metric = lambda *a, **k: None
    mod.get_metric = lambda *a, **k: 0
    mod.register_control_events = lambda *a, **k: None
    mod.update_dashboard_stat = lambda *a, **k: None
    sys.modules["core.dashboard"] = mod
    keygen = importlib.import_module("core.keygen")

    dummy_settings = types.SimpleNamespace(
        PUZZLE_MODE=True,
        PUZZLE_START=info["start"],
        PUZZLE_END=info["end"],
    )
    monkeypatch.setattr(keygen, "settings", dummy_settings, raising=False)
    for _ in range(50):
        seed = keygen.generate_random_seed()
        assert int(info["start"], 16) <= seed <= int(info["end"], 16)

