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
from typing import Dict, List, Optional

import base58
from ecdsa import SECP256k1, SigningKey
from eth_hash.auto import keccak
from eth_utils import to_checksum_address

from core.vanity_io import RollingAtomicWriter
from config import settings
from .deriv_paths import COIN_INFO, SUPPORTED_COINS, resolve_paths

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
# Encoding helpers
# ---------------------------------------------------------------------------


def privkey_to_pubkey(priv: bytes, compressed: bool = True) -> bytes:
    """Return the SEC1 encoded public key for ``priv``."""
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    vk = sk.get_verifying_key()
    if compressed:
        prefix = b"\x02" if vk.pubkey.point.y() % 2 == 0 else b"\x03"
        return prefix + vk.to_string()[:32]
    else:
        return b"\x04" + vk.to_string()


def pubkey_to_p2pkh(pub: bytes, version: int = 0x00) -> str:
    """Convert ``pub`` to a base58 P2PKH address."""
    h160 = hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest()
    payload = bytes([version]) + h160
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def priv_to_wif(priv: bytes, version: int = 0x80, compressed: bool = True) -> str:
    """Encode ``priv`` into Wallet Import Format."""
    payload = bytes([version]) + priv + (b"\x01" if compressed else b"")
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def priv_to_btc(priv: bytes) -> tuple[str, str]:
    info = COIN_INFO["btc"]
    pub = privkey_to_pubkey(priv)
    addr = pubkey_to_p2pkh(pub, info.p2pkh_version)
    wif = priv_to_wif(priv, info.wif_version, True)
    return addr, wif


def priv_to_eth(priv: bytes) -> str:
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    pub = sk.get_verifying_key().to_string()
    addr = keccak(pub)[-20:]
    return to_checksum_address("0x" + addr.hex())


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
    )

    mnemonic = generate_mnemonic(num_words, wordlist, rng)
    seed = mnemonic_to_seed(mnemonic, args.passphrase)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    for coin in coins:
        priv = derive_private_key(seed, paths[coin])
        if coin == "btc":
            addr, wif = priv_to_btc(priv)
        elif coin == "eth":
            addr = priv_to_eth(priv)
            wif = priv.hex()
        else:
            # Future coins can plug in proper encoders here
            addr = priv.hex()
            wif = priv.hex()
        line = (
            f"{timestamp} | {mnemonic} | {args.passphrase or '-'} | coin={coin.upper()} | "
            f"path={paths[coin]} | addr={addr} | wif={wif} | funded=0"
        )
        writer.write_line(line)
    writer.close()

    print(f"Generated mnemonic written to {writer.final_path}")
