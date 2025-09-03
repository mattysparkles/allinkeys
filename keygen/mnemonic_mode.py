"""Mnemonic generation mode for AllInKeys.

This module implements a small subset of mnemonic based key generation:

* Random mnemonic creation from the BIP-39 English word list or a custom
  list supplied by the user.
* BIP-32/BIP-44 style private key derivation for arbitrary paths.
* Basic BTC (P2PKH) and ETH address encoding.

The implementation is intentionally minimal but provides clear extension
points for future GPU acceleration and additional coins.
"""

from __future__ import annotations

import os
import time
import hashlib
import hmac
import struct
import random
from typing import Dict, List, Optional, Set

from ecdsa import SECP256k1

from core.vanity_io import RollingAtomicWriter
from config import settings
from .deriv_paths import SUPPORTED_COINS, resolve_paths
from .encoders_mnemonic import encode_privkey, privkey_to_pubkey
from gpu.mnemonic_opencl import available_devices, pbkdf2_sha512
from utils.file_utils import find_latest_funded_file

# ---------------------------------------------------------------------------
# Word list helpers
# ---------------------------------------------------------------------------


def load_wordlist(custom_path: Optional[str] = None) -> List[str]:
    """Return list of words for mnemonic generation."""
    if custom_path:
        path = custom_path
    else:
        path = os.path.join(os.path.dirname(__file__), "bip39_english.txt")
    with open(path, "r", encoding="utf-8") as f:
        words = [w.strip() for w in f.readlines() if w.strip()]
    if not words:
        raise ValueError("Word list is empty")
    return words


def generate_mnemonic(num_words: int, wordlist: List[str], rng: random.Random) -> str:
    """Return a random mnemonic of ``num_words`` length."""
    return " ".join(rng.choice(wordlist) for _ in range(num_words))


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """Convert BIP-39 mnemonic to seed bytes."""
    salt = ("mnemonic" + passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha512", mnemonic.encode("utf-8"), salt, 2048)


# ---------------------------------------------------------------------------
# Funded address helpers
# ---------------------------------------------------------------------------


def _load_funded_sets(coins: List[str]) -> Dict[str, Set[str]]:
    """Load funded address sets for the specified ``coins``.

    The function looks up the most recent funded list for each coin using
    :func:`utils.file_utils.find_latest_funded_file`.  Missing files are
    silently ignored and result in an empty set.  Addresses are stored exactly
    as they appear in the file except for Ethereum addresses which are
    normalised to lowercase to match the encoder behaviour.
    """

    funded: Dict[str, Set[str]] = {}
    for coin in coins:
        funded[coin] = set()
        file_path = find_latest_funded_file(coin)
        if not file_path:
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    addr = line.strip()
                    if not addr:
                        continue
                    if coin == "eth":
                        addr = addr.lower()
                    funded[coin].add(addr)
        except Exception:
            # Missing or unreadable files simply result in an empty set which is
            # treated as "no funded addresses" for that coin.
            continue
    return funded


# ---------------------------------------------------------------------------
# BIP32 derivation helpers
# ---------------------------------------------------------------------------

_BIP32_HARDEN = 0x80000000


def _ser32(i: int) -> bytes:
    return struct.pack(">L", i)


def _parse_path(path: str) -> List[int]:
    if path in ("m", "M", ""):  # root
        return []
    if not path.startswith("m/"):
        raise ValueError(f"Invalid derivation path: {path}")
    result = []
    for p in path.lstrip("m/").split("/"):
        if p.endswith("'"):
            result.append(int(p[:-1]) | _BIP32_HARDEN)
        else:
            result.append(int(p))
    return result


def _master_key_from_seed(seed: bytes) -> tuple[bytes, bytes]:
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return I[:32], I[32:]


def _ckd_priv(k_par: bytes, c_par: bytes, index: int) -> tuple[bytes, bytes]:
    if index & _BIP32_HARDEN:
        data = b"\x00" + k_par + _ser32(index)
    else:
        data = privkey_to_pubkey(k_par) + _ser32(index)
    I = hmac.new(c_par, data, hashlib.sha512).digest()
    k_int = (int.from_bytes(I[:32], "big") + int.from_bytes(k_par, "big")) % SECP256k1.order
    return k_int.to_bytes(32, "big"), I[32:]


def derive_private_key(seed: bytes, path: str) -> bytes:
    """Derive a private key for ``path`` from ``seed``."""
    k, c = _master_key_from_seed(seed)
    for index in _parse_path(path):
        k, c = _ckd_priv(k, c, index)
    return k


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_mnemonic_mode(args) -> None:
    """Entry point invoked from :mod:`main` when ``--mnemonic`` is passed."""

    wordlist = load_wordlist(args.custom_words_file)
    num_words = args.num_words or 12
    rng = random.Random(args.rng_seed)

    # Determine coins
    if getattr(args, "allcoins", False):
        coins = SUPPORTED_COINS
    elif getattr(args, "coins", None):
        coins = [c.lower() for c in args.coins]
    else:
        coins = ["btc"]

    # Determine preset
    preset = None
    for name in ["atomic", "coinomi", "ledger", "trust", "trezor"]:
        if getattr(args, name, False):
            preset = name
            break

    overrides: Dict[str, str] = {}
    for coin in SUPPORTED_COINS:
        path_override = getattr(args, f"{coin}_path", None)
        if path_override:
            overrides[coin] = path_override
    paths = resolve_paths(coins, preset=preset, global_path=getattr(args, "global_path", None), overrides=overrides)

    writer = RollingAtomicWriter(
        settings.VANITY_TXT_DIR,
        rotate_lines=settings.VANITY_ROTATE_LINES,
        max_bytes=settings.VANITY_MAX_BYTES,
        prefix="mnemonic_output",
        rotate_seconds=60,
    )

    # Load funded address data if requested.  Missing files simply yield empty
    # sets so the rest of the pipeline can continue unhindered.
    funded_sets = _load_funded_sets(coins) if getattr(args, "funded", False) else {c: set() for c in coins}

    # GPU acceleration is optional; we only attempt to use it when a specific
    # device is requested and at least one OpenCL device is present.
    gpu_devices = available_devices()

    iterations = getattr(args, "iterations", 0) or 0
    produced = 0
    try:
        while iterations <= 0 or produced < iterations:
            mnemonic = generate_mnemonic(num_words, wordlist, rng)
            if gpu_devices and getattr(args, "gpu_id", None) is not None:
                seed = pbkdf2_sha512(mnemonic, args.passphrase, device_id=args.gpu_id)
            else:
                seed = mnemonic_to_seed(mnemonic, args.passphrase)
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

            for coin in coins:
                priv = derive_private_key(seed, paths[coin])
                addr, wif = encode_privkey(coin, priv)
                normalized = addr.lower() if coin == "eth" else addr
                funded_flag = 1 if normalized in funded_sets.get(coin, set()) else 0
                line = (
                    f"{timestamp} | {mnemonic} | {args.passphrase or '-'} | coin={coin.upper()} | "
                    f"path={paths[coin]} | addr={addr} | wif={wif} | funded={funded_flag}"
                )
                writer.write_line(line)

            produced += 1
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
        print(f"Generated mnemonics written to {writer.final_path}")
