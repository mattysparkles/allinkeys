import argparse
from unittest.mock import patch
import argparse
from unittest.mock import patch
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.modules.setdefault('pyopencl', types.SimpleNamespace(get_platforms=lambda: [], device_type=types.SimpleNamespace(GPU=0)))
sys.modules.setdefault('core.altcoin_derive', types.SimpleNamespace(start_altcoin_conversion_process=lambda *a, **k: None))
# Stub optional environment dependencies
sys.modules.setdefault('dotenv', types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault(
    'psutil',
    types.SimpleNamespace(
        cpu_percent=lambda: 0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0, used=0, total=0),
        disk_usage=lambda p: types.SimpleNamespace(free=0),
    ),
)
# Stub out heavy modules so ``import main`` does not require optional deps
sys.modules.setdefault('core.checkpoint', types.SimpleNamespace(load_keygen_checkpoint=lambda *a, **k: None,
                                                               save_keygen_checkpoint=lambda *a, **k: None))
sys.modules.setdefault('core.downloader', types.SimpleNamespace(download_and_compare_address_lists=lambda *a, **k: None,
                                                               generate_test_csv=lambda *a, **k: None))
sys.modules.setdefault('core.csv_checker', types.SimpleNamespace(check_csvs_day_one=lambda *a, **k: None,
                                                                check_csvs=lambda *a, **k: None))
sys.modules.setdefault('core.alerts', types.SimpleNamespace(trigger_startup_alerts=lambda *a, **k: None,
                                                            alert_match=lambda *a, **k: None))
sys.modules.setdefault('core.dashboard', types.SimpleNamespace(update_dashboard_stat=lambda *a, **k: None,
                                                              _default_metrics={},
                                                              init_shared_metrics=lambda *a, **k: None,
                                                              init_dashboard_manager=lambda *a, **k: None,
                                                              get_current_metrics=lambda *a, **k: {},
                                                              get_metric=lambda *a, **k: None,
                                                              set_metric=lambda *a, **k: None,
                                                              warn_rate_limited=lambda *a, **k: None))
sys.modules.setdefault('ui.dashboard_gui', types.SimpleNamespace(start_dashboard=lambda *a, **k: None))
sys.modules.setdefault(
    'core.gpu_selector',
    types.SimpleNamespace(
        assign_gpu_roles=lambda *a, **k: None,
        get_vanitysearch_gpu_ids=lambda: [],
        get_altcoin_gpu_ids=lambda: [],
        get_gpu_assignments=lambda: {},
    ),
)
sys.modules.setdefault('core.keygen', types.SimpleNamespace(run_btc_only=lambda *a, **k: None))
sys.modules.setdefault('core.btc_only_checker', types.SimpleNamespace(btc_only_checker_loop=lambda *a, **k: None))
sys.modules.setdefault('core.telemetry', types.SimpleNamespace(start_telemetry=lambda *a, **k: None))

import main
main.metrics_updater = lambda *a, **k: None


def _make_args(**kwargs):
    defaults = dict(
        only=None,
        only_legacy=None,
        compressed=False,
        uncompressed=False,
        addr_format='compressed',
        skip_downloads=False,
        no_dashboard=False,
        headless=False,
        enable_bc1=False,
        bc1=False,
        vanity_prefix=None,
        case_insensitive=False,
        all=False,
        funded=False,
        all_legacy=False,
        funded_legacy=False,
        puzzle=None,
        every=False,
        target=False,
        gpu_index=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_btc_only_default_compressed():
    args = _make_args(only=['btc'])
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once()
        assert run_mock.call_args.kwargs.get('compressed') is True


def test_btc_only_explicit_uncompressed():
    args = _make_args(only=['btc'], uncompressed=True)
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once()
        assert run_mock.call_args.kwargs.get('compressed') is False


def test_btc_only_addr_format_uncompressed():
    args = _make_args(only=['btc'], addr_format='uncompressed')
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once()
        assert run_mock.call_args.kwargs.get('compressed') is False


def test_legacy_only_flag_emits_warning(capsys):
    args = _make_args(only_legacy=['btc'])
    with patch('core.keygen.run_btc_only') as run_mock:
        main.handle_deprecated_flags(args)
        main.run_only_mode(args)
        run_mock.assert_called_once()
        assert run_mock.call_args.kwargs.get('compressed') is True
    assert 'deprecated' in capsys.readouterr().err.lower()


def test_parser_accepts_only_and_compressed():
    parser = main.build_parser()
    args = parser.parse_args(["--only", "btc", "--compressed"])
    assert args.only == ["btc"]
    assert args.compressed is True


def test_parser_accepts_multiple_only():
    parser = main.build_parser()
    args = parser.parse_args(["--only", "btc,ltc"])
    assert args.only == ["btc", "ltc"]


def test_parser_no_telemetry_flag():
    parser = main.build_parser()
    args = parser.parse_args(["--no-telemetry"])
    assert args.no_telemetry is True


def test_parser_telemetry_default_enabled():
    parser = main.build_parser()
    args = parser.parse_args([])
    assert getattr(args, "no_telemetry") is False


def test_deprecated_all_flag_warning(capsys):
    args = _make_args(all_legacy=True)
    main.handle_deprecated_flags(args)
    assert args.all is True
    assert 'deprecated' in capsys.readouterr().err.lower()


def test_deprecated_funded_flag_warning(capsys):
    args = _make_args(funded_legacy=True)
    main.handle_deprecated_flags(args)
    assert args.funded is True
    assert 'deprecated' in capsys.readouterr().err.lower()
