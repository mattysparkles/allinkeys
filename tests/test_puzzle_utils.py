import types
import argparse
import sys

# Stub optional environment dependencies and heavy modules
sys.modules.setdefault('dotenv', types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault(
    'psutil',
    types.SimpleNamespace(
        cpu_percent=lambda: 0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0, used=0, total=0),
        disk_usage=lambda p: types.SimpleNamespace(free=0),
    ),
)
sys.modules.setdefault('pyopencl', types.SimpleNamespace(get_platforms=lambda: [], device_type=types.SimpleNamespace(GPU=0)))
sys.modules.setdefault('core.altcoin_derive', types.SimpleNamespace(start_altcoin_conversion_process=lambda *a, **k: None))
sys.modules.setdefault('core.checkpoint', types.SimpleNamespace(load_keygen_checkpoint=lambda *a, **k: None, save_keygen_checkpoint=lambda *a, **k: None))
sys.modules.setdefault('core.downloader', types.SimpleNamespace(download_and_compare_address_lists=lambda *a, **k: None, generate_test_csv=lambda *a, **k: None))
sys.modules.setdefault('core.csv_checker', types.SimpleNamespace(check_csvs_day_one=lambda *a, **k: None, check_csvs=lambda *a, **k: None))
sys.modules.setdefault('core.alerts', types.SimpleNamespace(trigger_startup_alerts=lambda *a, **k: None, alert_match=lambda *a, **k: None))
sys.modules.setdefault('core.dashboard', types.SimpleNamespace(update_dashboard_stat=lambda *a, **k: None, _default_metrics={}, init_shared_metrics=lambda *a, **k: None, init_dashboard_manager=lambda *a, **k: None, get_current_metrics=lambda *a, **k: {}, get_metric=lambda *a, **k: None, set_metric=lambda *a, **k: None, warn_rate_limited=lambda *a, **k: None))
sys.modules.setdefault('ui.dashboard_gui', types.SimpleNamespace(start_dashboard=lambda *a, **k: None))
sys.modules.setdefault('core.gpu_selector', types.SimpleNamespace(assign_gpu_roles=lambda *a, **k: None, get_vanitysearch_gpu_ids=lambda: [], get_altcoin_gpu_ids=lambda: [], get_gpu_assignments=lambda: {}))
sys.modules.setdefault('core.keygen', types.SimpleNamespace(run_btc_only=lambda *a, **k: None))
sys.modules.setdefault('core.btc_only_checker', types.SimpleNamespace(btc_only_checker_loop=lambda *a, **k: None))

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

