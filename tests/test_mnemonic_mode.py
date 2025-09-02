import argparse
import pathlib
import sys
import types
import random

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

import keygen.mnemonic_mode as mnemonic_mode
from keygen.mnemonic_mode import (
    mnemonic_to_seed,
    generate_mnemonic,
    load_wordlist,
    derive_private_key,
    run_mnemonic_mode,
)
from keygen.encoders_mnemonic import encode_privkey, priv_to_btc, priv_to_eth
from keygen.deriv_paths import resolve_paths
from config import settings
from utils import file_utils
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


def test_additional_coin_encoders():
    priv = (1).to_bytes(32, "big")
    vectors = {
        "ltc": (
            "LVuDpNCSSj6pQ7t9Pv6d6sUkLKoqDEVUnJ",
            "T33ydQRKp4FCW5LCLLUB7deioUMoveiwekdwUwyfRDeGZm76aUjV",
        ),
        "bch": (
            "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH",
            "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn",
        ),
        "dash": (
            "XmN7PQYWKn5MJFna5fRYgP6mxT2F7xpekE",
            "XBHddvWWiMu3nZhhpTXBQWJMmdz5JNKJD85b9fgKAckCT2coW3Y4",
        ),
        "doge": (
            "DFpN6QqFfUm3gKNaxN6tNcab1FArL9cZLE",
            "QNcdLVw8fHkixm6NNyN6nVwxKek4u7qrioRbQmjxac5TVoTtZuot",
        ),
        "pep": (
            "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf",
            priv.hex(),
        ),
        "rvn": (
            "RKxTdfmtxtfLDKZBgx6SvNkBtNu9jRYnLh",
            "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn",
        ),
    }
    for coin, (addr, wif) in vectors.items():
        got_addr, got_wif = encode_privkey(coin, priv)
        assert got_addr == addr
        assert got_wif == wif


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


def test_run_mnemonic_mode_marks_funded(tmp_path, monkeypatch):
    """Ensure mnemonic mode flags funded addresses when lists are available."""

    # Redirect output directory to a temporary location for isolation
    monkeypatch.setattr(settings, "VANITY_TXT_DIR", str(tmp_path))

    # Determine deterministic mnemonic and corresponding BTC address
    rng_seed = 1234
    wordlist = load_wordlist(None)
    rng = random.Random(rng_seed)
    mnemonic = generate_mnemonic(12, wordlist, rng)
    seed = mnemonic_to_seed(mnemonic, "")
    paths = resolve_paths(["btc"])
    priv = derive_private_key(seed, paths["btc"])
    addr, _ = encode_privkey("btc", priv)

    # Create a fake funded list containing this address
    funded_file = tmp_path / "BTC_addresses_test.txt"
    funded_file.write_text(addr + "\n")
    monkeypatch.setattr(
        mnemonic_mode,
        "find_latest_funded_file",
        lambda coin, directory=file_utils.DOWNLOADS_DIR, unique=False: str(funded_file)
        if coin == "btc" else None,
    )

    # Build argument namespace expected by run_mnemonic_mode
    args = argparse.Namespace(
        num_words=12,
        custom_words_file=None,
        rng_seed=rng_seed,
        passphrase="",
        allcoins=False,
        coins=["btc"],
        global_path=None,
        btc_path=None,
        eth_path=None,
        ltc_path=None,
        bch_path=None,
        doge_path=None,
        dash_path=None,
        rvn_path=None,
        pep_path=None,
        atomic=False,
        coinomi=False,
        ledger=False,
        trust=False,
        trezor=False,
        mnemonic=True,
        funded=True,
        gpu_id=None,
        threads=1,
    )

    run_mnemonic_mode(args)

    output_files = list(tmp_path.glob("mnemonic_output_*.txt"))
    assert len(output_files) == 1
    contents = output_files[0].read_text()
    assert "funded=1" in contents
