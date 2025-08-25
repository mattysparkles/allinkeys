import argparse
from unittest.mock import patch
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.modules.setdefault('pyopencl', types.SimpleNamespace(get_platforms=lambda: [], device_type=types.SimpleNamespace(GPU=0)))
sys.modules.setdefault('core.altcoin_derive', types.SimpleNamespace(start_altcoin_conversion_process=lambda *a, **k: None))
import main


def _make_args(**kwargs):
    defaults = dict(
        only=None,
        only_legacy=None,
        compressed=False,
        uncompressed=False,
        addr_format='compressed',
        skip_downloads=False,
        no_dashboard=False,
        enable_bc1=False,
        all=False,
        funded=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_btc_only_default_compressed():
    args = _make_args(only='btc')
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once_with(compressed=True)


def test_btc_only_explicit_uncompressed():
    args = _make_args(only='btc', uncompressed=True)
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once_with(compressed=False)


def test_btc_only_addr_format_uncompressed():
    args = _make_args(only='btc', addr_format='uncompressed')
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once_with(compressed=False)


def test_legacy_only_flag_emits_warning(capsys):
    args = _make_args(only_legacy='btc')
    with patch('core.keygen.run_btc_only') as run_mock:
        main.run_only_mode(args)
        run_mock.assert_called_once_with(compressed=True)
    assert 'deprecated' in capsys.readouterr().err.lower()
