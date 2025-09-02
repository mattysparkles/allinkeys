import argparse
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Stub optional dependencies so importing ``main`` does not pull heavy
# modules or require external binaries.  The stubs mirror those used in the
# existing CLI tests.
sys.modules.setdefault('pyopencl', types.SimpleNamespace(get_platforms=lambda: [], device_type=types.SimpleNamespace(GPU=0)))
sys.modules.setdefault('core.altcoin_derive', types.SimpleNamespace(start_altcoin_conversion_process=lambda *a, **k: None))
sys.modules.setdefault('dotenv', types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault(
    'psutil',
    types.SimpleNamespace(
        cpu_percent=lambda: 0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0, used=0, total=0),
        disk_usage=lambda p: types.SimpleNamespace(free=0),
    ),
)
sys.modules.setdefault('core.checkpoint', types.SimpleNamespace(load_keygen_checkpoint=lambda *a, **k: None, save_keygen_checkpoint=lambda *a, **k: None))
sys.modules.setdefault('core.downloader', types.SimpleNamespace(download_and_compare_address_lists=lambda *a, **k: None, generate_test_csv=lambda *a, **k: None))
sys.modules.setdefault('core.csv_checker', types.SimpleNamespace(check_csvs_day_one=lambda *a, **k: None, check_csvs=lambda *a, **k: None))
sys.modules.setdefault('core.alerts', types.SimpleNamespace(trigger_startup_alerts=lambda *a, **k: None, alert_match=lambda *a, **k: None))
sys.modules.setdefault('core.dashboard', types.SimpleNamespace(update_dashboard_stat=lambda *a, **k: None, _default_metrics={}, init_shared_metrics=lambda *a, **k: None, init_dashboard_manager=lambda *a, **k: None, get_current_metrics=lambda *a, **k: {}, get_metric=lambda *a, **k: None, set_metric=lambda *a, **k: None, warn_rate_limited=lambda *a, **k: None))
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

from keygen.mnemonic_mode import mnemonic_to_seed, priv_to_btc, priv_to_eth
from keygen.deriv_paths import resolve_paths
import main


def test_bip39_seed_vector():
    mnem = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    seed = mnemonic_to_seed(mnem, "TREZOR")
    assert (
        seed.hex()
        == "2e8905819b8723fe2c1d161860e5ee1830318dbf49a83bd451cfb8440c28bd6fa457fe1296106559a3c80937a1c1069be3a3a5bd381ee6260e8d9739fce1f607"
    )


def test_btc_address_from_privkey():
    priv = (1).to_bytes(32, "big")
    addr, wif = priv_to_btc(priv)
    assert addr == "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH"
    assert wif == "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn"


def test_eth_address_from_privkey():
    priv = (1).to_bytes(32, "big")
    assert priv_to_eth(priv) == "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"


def test_resolve_paths_preset_and_override():
    paths = resolve_paths(["btc", "eth"], preset="atomic", global_path="m/0/0", overrides={"eth": "m/44'/60'/0'/0/1"})
    assert paths["btc"] == "m/44'/0'/0'/0/0"
    assert paths["eth"] == "m/44'/60'/0'/0/1"


def test_cli_parses_mnemonic_flags():
    parser = main.build_parser()
    args = parser.parse_args(["--mnemonic", "--12words", "--coins", "btc,eth", "--atomic"])
    assert args.mnemonic is True
    assert args.num_words == 12
    assert args.coins == ["btc", "eth"]
    assert args.atomic is True
