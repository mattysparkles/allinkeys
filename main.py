# main.py

import os
import io
import sys
import argparse
import subprocess
from pathlib import Path

# Ensure writable base/log directories when running as a frozen Windows exe.
if getattr(sys, "frozen", False):
    install_dir = Path(sys.executable).resolve().parent
    os.environ.setdefault("ALLINKEYS_ASSETS_DIR", str(install_dir))
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_path = Path(local_app_data) if local_app_data else Path.home()
    base_dir = str(base_path / "AllInKeys")
    os.environ.setdefault("ALLINKEYS_BASE_DIR", base_dir)
    os.environ.setdefault("ALLINKEYS_LOG_DIR", str(Path(base_dir) / "logs"))

import config.settings as settings
from config.settings import find_vanitysearch_binary
from core.modes import btc_only, vanity, mnemonic
from core.logger import get_logger

# Wrap stdout once with UTF-8 encoding if not already wrapped
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


COIN_OPTIONS = ["btc", "bch", "ltc", "doge", "dash", "rvn", "pep", "eth"]


# External plugins can register additional modes via:
# PLUGIN_REGISTRY["custom_mode"] = CustomPlugin()
PLUGIN_REGISTRY = {
    "btc_only": btc_only,
    "vanity": vanity,
    "mnemonic": mnemonic,
}

logger = get_logger(__name__)


def _parse_only(value: str) -> list[str]:
    coins = [c.strip().lower() for c in value.split(",") if c.strip()]
    for c in coins:
        if c not in COIN_OPTIONS:
            raise argparse.ArgumentTypeError(f"Unknown coin option: {c}")
    return coins


def handle_deprecated_flags(args):
    if getattr(args, "only_legacy", None) and not getattr(args, "only", None):
        args.only = args.only_legacy
        print("Warning: '-only' is deprecated; use '--only' instead.", file=sys.stderr)
    if getattr(args, "all_legacy", False) and not getattr(args, "all", False):
        args.all = True
        print("Warning: '-all' is deprecated; use '--all' instead.", file=sys.stderr)
    if getattr(args, "funded_legacy", False) and not getattr(args, "funded", False):
        args.funded = True
        print(
            "Warning: '-funded' is deprecated; use '--funded' instead.", file=sys.stderr
        )


def handle_puzzle_mode(args):
    if getattr(args, "puzzle", None) is None:
        return
    from utils.puzzle import get_puzzle_info

    info = get_puzzle_info(args.puzzle)
    settings.PUZZLE_MODE = True
    settings.PUZZLE_NUMBER = args.puzzle
    settings.PUZZLE_START = info["start"]
    settings.PUZZLE_END = info["end"]
    settings.PUZZLE_CHUNK_INDEX = getattr(args, "chunk", None)
    if getattr(args, "every", False):
        settings.VANITY_PATTERN = "1**"
    else:
        settings.VANITY_PATTERN = info["address"]
    args.compressed = True


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level :class:`argparse.ArgumentParser`.

    The parser construction is factored out so tests can easily verify CLI
    behaviour without invoking the full application.
    """

    parser = argparse.ArgumentParser(description="AllInKeys Modular Runner")
    parser.add_argument(
        "--mode",
        default="vanity",
        choices=sorted(PLUGIN_REGISTRY.keys()),
        help="Select the execution mode (default: vanity)",
    )
    parser.add_argument(
        "--skip-backlog", action="store_true", help="Skip backlog conversion on startup"
    )
    parser.add_argument(
        "--no-dashboard", action="store_true", help="Don't launch GUI dashboard"
    )
    parser.add_argument(
        "--dashboard-password", help="Password required to access the dashboard"
    )
    parser.add_argument(
        "--skip-downloads", action="store_true", help="Skip downloading balance files"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run without any GUI or visuals"
    )
    parser.add_argument(
        "--no-telemetry", action="store_true", help="Disable telemetry reporting"
    )
    parser.add_argument(
        "--telemetry-setup",
        action="store_true",
        help="Run the telemetry setup wizard",
    )
    parser.add_argument(
        "--auth-token",
        help="Bearer token used for authenticated telemetry endpoints",
    )
    parser.add_argument(
        "--control-url",
        help="Override the machine control polling endpoint",
    )
    parser.add_argument(
        "--match-test", action="store_true", help="Trigger fake match alert on startup"
    )
    parser.add_argument(
        "--purge",
        nargs="?",
        const="30",
        metavar="DAYS",
        help="Remove files older than DAYS (default 30) in output/vanity_output/ and output/csv/",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview purge actions without deleting"
    )
    parser.add_argument(
        "--enable-bc1", action="store_true", help="Enable bc1/bech32 address generation"
    )
    parser.add_argument(
        "--bc1",
        action="store_true",
        help="Use bc1/bech32 funded list and force bc1 vanity pattern (disables legacy P2PKH)",
    )
    parser.add_argument(
        "--only",
        type=_parse_only,
        dest="only",
        help="Restrict to coin flow(s). Comma-separated list.",
    )
    parser.add_argument(
        "-only", type=_parse_only, dest="only_legacy", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--puzzle", type=int, help="Run BTC puzzle mode for given puzzle number"
    )
    parser.add_argument(
        "--chunk", type=int, help="Puzzle mode: claim specific chunk index"
    )
    parser.add_argument(
        "--gpu-index", type=int, help="Force use of a specific GPU device index"
    )
    parser.add_argument(
        "-v",
        "--vanity",
        dest="vanity_prefix",
        help="Custom vanity prefix/pattern for VanitySearch (e.g., 1nasty)",
    )
    parser.add_argument(
        "-q",
        "--case-insensitive",
        action="store_true",
        help="Use case-insensitive vanity matching",
    )
    puzzle_group = parser.add_mutually_exclusive_group()
    puzzle_group.add_argument(
        "--every", action="store_true", help="Puzzle mode: keep generic '1**' prefix"
    )
    puzzle_group.add_argument(
        "--target",
        action="store_true",
        help="Puzzle mode: target puzzle address (default)",
    )
    parser.add_argument(
        "--addr-format",
        choices=["compressed", "uncompressed"],
        default="compressed",
        help="BTC-only address format (default: compressed)",
    )
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--compressed", action="store_true", help="BTC-only: force compressed addresses"
    )
    fmt_group.add_argument(
        "--uncompressed",
        action="store_true",
        help="BTC-only: force uncompressed addresses",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="Use 'all BTC addresses ever used' range mode",
    )
    mode.add_argument("--funded", action="store_true", help="Use daily funded BTC list")
    mode.add_argument(
        "-all", dest="all_legacy", action="store_true", help=argparse.SUPPRESS
    )
    mode.add_argument(
        "-funded", dest="funded_legacy", action="store_true", help=argparse.SUPPRESS
    )

    # ------------------------------------------------------------------
    # Mnemonic mode flags
    # ------------------------------------------------------------------
    mnemonic_group = parser.add_argument_group(
        "Mnemonic Mode",
        "Generate BIP-39 mnemonic phrases and derive addresses without running VanitySearch",
    )
    mnemonic_group.add_argument(
        "--mnemonic",
        action="store_true",
        help="Enable mnemonic generation mode (skip VanitySearch)",
    )
    for i in range(3, 26):
        mnemonic_group.add_argument(
            f"--{i}words",
            dest="num_words",
            action="store_const",
            const=i,
            help=f"Generate {i}-word mnemonic phrase",
        )

    mnemonic_group.add_argument(
        "--bip39",
        action="store_true",
        help="Use the default BIP39 English wordlist",
    )
    mnemonic_group.add_argument(
        "--custom-words-file",
        help="Path to a custom word list for mnemonic generation",
    )
    lang_group = mnemonic_group.add_mutually_exclusive_group()
    lang_group.add_argument(
        "--spanish",
        action="store_true",
        help="Use BIP39 Spanish wordlist",
    )
    lang_group.add_argument(
        "--french",
        action="store_true",
        help="Use BIP39 French wordlist",
    )
    lang_group.add_argument(
        "--italian",
        action="store_true",
        help="Use BIP39 Italian wordlist",
    )
    lang_group.add_argument(
        "--japanese",
        action="store_true",
        help="Use BIP39 Japanese wordlist",
    )
    lang_group.add_argument(
        "--korean",
        action="store_true",
        help="Use BIP39 Korean wordlist",
    )
    lang_group.add_argument(
        "--czech",
        action="store_true",
        help="Use BIP39 Czech wordlist",
    )
    lang_group.add_argument(
        "--portuguese",
        action="store_true",
        help="Use BIP39 Portuguese wordlist",
    )
    lang_group.add_argument(
        "--chinese",
        action="store_true",
        help="Use BIP39 Traditional Chinese wordlist",
    )
    lang_group.add_argument(
        "--chinese-simple",
        action="store_true",
        help="Use BIP39 Simplified Chinese wordlist",
    )
    mnemonic_group.add_argument(
        "--coins",
        type=_parse_only,
        help="Comma-separated list of coins to derive (e.g., btc,eth)",
    )
    mnemonic_group.add_argument(
        "--allcoins",
        action="store_true",
        help="Derive all supported coins",
    )
    mnemonic_group.add_argument(
        "--atomic",
        action="store_true",
        help="Use Atomic wallet derivation paths",
    )
    mnemonic_group.add_argument(
        "--coinomi",
        action="store_true",
        help="Use Coinomi wallet paths",
    )
    mnemonic_group.add_argument(
        "--ledger",
        action="store_true",
        help="Use Ledger wallet paths",
    )
    mnemonic_group.add_argument(
        "--trust",
        action="store_true",
        help="Use Trust wallet paths",
    )
    mnemonic_group.add_argument(
        "--trezor",
        action="store_true",
        help="Use Trezor wallet paths",
    )
    mnemonic_group.add_argument(
        "--path",
        dest="global_path",
        help="Custom derivation path for all coins",
    )
    for _coin in ["btc", "bch", "ltc", "eth", "dash", "doge", "pep", "rvn"]:
        mnemonic_group.add_argument(
            f"--{_coin}-path",
            dest=f"{_coin}_path",
            help=f"Custom derivation path for {_coin.upper()}",
        )
    mnemonic_group.add_argument(
        "--gpu",
        action="store_true",
        help="Enable OpenCL acceleration if available",
    )
    mnemonic_group.add_argument(
        "--gpu-id",
        type=int,
        help="Select specific GPU device for mnemonic mode",
    )
    mnemonic_group.add_argument(
        "--no-gpu",
        action="store_true",
        help="Force the CPU implementation",
    )
    mnemonic_group.add_argument(
        "--rng-seed",
        type=int,
        help="Deterministic RNG seed for mnemonics",
    )
    mnemonic_group.add_argument(
        "--passphrase",
        default="",
        help="Optional BIP39 passphrase",
    )
    mnemonic_group.add_argument(
        "--rate-limit",
        type=int,
        help="Throttle derivations per second",
    )
    mnemonic_group.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Mnemonic mode batch size",
    )
    mnemonic_group.add_argument(
        "--threads",
        type=int,
        default=1,
        help="CPU threads for mnemonic mode",
    )
    mnemonic_group.add_argument(
        "--progress-interval",
        type=int,
        default=10,
        help="Progress update interval in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point used by ``__main__`` and tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "telemetry_setup", False):
        from core.telemetry import run_telemetry_setup, _is_interactive

        interactive = _is_interactive() and not getattr(args, "headless", False)
        if not interactive:
            print(
                "[Telemetry] Telemetry setup requires an interactive terminal.",
                flush=True,
            )
        else:
            run_telemetry_setup(force=True)
    if getattr(args, "no_telemetry", False):
        settings.SEED_TELEMETRY_ENABLED = False
    if getattr(args, "auth_token", None):
        os.environ["AUTH_TOKEN"] = args.auth_token
    if getattr(args, "control_url", None):
        os.environ["CONTROL_ENDPOINT"] = args.control_url
    # Handle retention purge early and exit
    if getattr(args, "purge", None) is not None:
        try:
            days = int(args.purge)
        except Exception:
            print("Invalid DAYS for --purge; must be an integer.", flush=True)
            return 2
        from core.utils.retention import purge_older_than

        _, messages = purge_older_than(days, dry_run=getattr(args, "dry_run", False))
        for m in messages:
            print(m, flush=True)
        return 0
    if getattr(args, "dashboard_password", None):
        from utils.auth import hash_password

        settings.DASHBOARD_PASSWORD_HASH = hash_password(args.dashboard_password)
    handle_deprecated_flags(args)

    argv_list = list(argv) if argv is not None else sys.argv[1:]
    explicit_mode = "--mode" in argv_list
    if not explicit_mode:
        if getattr(args, "mnemonic", False):
            args.mode = "mnemonic"
        elif getattr(args, "only", None):
            args.mode = "btc_only"
        elif getattr(args, "puzzle", None) is not None:
            args.mode = "btc_only"

    handle_puzzle_mode(args)
    if getattr(args, "case_insensitive", False):
        settings.VANITY_CASE_INSENSITIVE = True
    if getattr(args, "bc1", False):
        settings.BTC_FUNDED_ADDRESS_MODE = "bech32"
        settings.ENABLE_P2WPKH = True
        settings.ENABLE_TAPROOT = True
        settings.ENABLE_P2PKH = False
        if not settings.PUZZLE_MODE and not getattr(args, "vanity_prefix", None):
            settings.VANITY_PATTERN = "bc1**"
    if getattr(args, "enable_bc1", False):
        settings.ENABLE_P2WPKH = True
        settings.ENABLE_TAPROOT = True
    if getattr(args, "vanity_prefix", None) and not settings.PUZZLE_MODE:
        settings.VANITY_PATTERN = args.vanity_prefix

    if args.mode not in PLUGIN_REGISTRY:
        print(f"Unknown mode '{args.mode}'.", file=sys.stderr)
        return 2

    missing_integrations = settings.get_missing_optional_integrations()
    if missing_integrations:
        import multiprocessing as mp

        if mp.current_process().name == "MainProcess":
            logger.info(
                "[Startup] Optional integrations disabled: %s. Configure them later in config/settings.py.",
                ", ".join(missing_integrations),
            )

    logger.info("[Startup] Mode selected: %s", args.mode)
    if args.mode == "vanity":
        logger.info("[Startup] VanitySearch/keygen pipeline enabled")
        if settings.ENABLE_BACKLOG_CONVERSION and not getattr(args, "skip_backlog", False):
            logger.info("[Startup] Altcoin derive pipeline enabled")
        if settings.ENABLE_DAY_ONE_CHECK or settings.ENABLE_UNIQUE_RECHECK:
            logger.info("[Startup] CSV checking pipeline enabled")
    elif args.mode == "btc_only":
        logger.info("[Startup] BTC-only VanitySearch pipeline enabled")
    elif args.mode == "mnemonic":
        logger.info("[Startup] Mnemonic derivation pipeline enabled")

    from core.dashboard import init_dashboard_manager

    shared_metrics = init_dashboard_manager()
    result = PLUGIN_REGISTRY[args.mode].start(shared_metrics, args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    import multiprocessing as mp

    mp.freeze_support()
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # already set

    vanity_path = find_vanitysearch_binary()
    if not vanity_path and os.name != "nt":
        try:
            subprocess.run(
                ["apt-get", "update"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["apt-get", "install", "-y", "vanitysearch"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        vanity_path = find_vanitysearch_binary()
    if not vanity_path:
        raise FileNotFoundError(
            "VanitySearch binary not found. Please install VanitySearch."
        )
    else:
        print(f"✅ VanitySearch found: {vanity_path}", flush=True)

    sys.exit(main())
