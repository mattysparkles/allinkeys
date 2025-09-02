"""Coin encoding utilities for mnemonic mode.

This module provides deterministic helpers to convert private keys into
addresses and WIF/hex representations for the coins supported by
``mnemonic_mode``.  The functions are intentionally pure – they perform no
file or network I/O – so they can be reused by both the producer and
consumer parts of the mnemonic pipeline.
"""
from __future__ import annotations

import hashlib
from typing import Tuple

import base58
from ecdsa import SECP256k1, SigningKey
from eth_hash.auto import keccak
from eth_utils import to_checksum_address

from .deriv_paths import COIN_INFO


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def privkey_to_pubkey(priv: bytes, compressed: bool = True) -> bytes:
    """Return the SEC1 encoded public key for ``priv``."""
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    vk = sk.get_verifying_key()
    if compressed:
        prefix = b"\x02" if vk.pubkey.point.y() % 2 == 0 else b"\x03"
        return prefix + vk.to_string()[:32]
    return b"\x04" + vk.to_string()


def pubkey_to_p2pkh(pub: bytes, version: int) -> str:
    """Convert ``pub`` to a base58 P2PKH address with ``version`` byte."""
    h160 = hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest()
    payload = bytes([version]) + h160
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def priv_to_wif(priv: bytes, version: int, compressed: bool = True) -> str:
    """Encode ``priv`` into Wallet Import Format using ``version``."""
    payload = bytes([version]) + priv + (b"\x01" if compressed else b"")
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


# ---------------------------------------------------------------------------
# Coin specific wrappers
# ---------------------------------------------------------------------------


def _p2pkh_coin(priv: bytes, coin: str) -> Tuple[str, str]:
    """Return ``(address, wif)`` for P2PKH style coins."""
    info = COIN_INFO[coin]
    pub = privkey_to_pubkey(priv, True)
    addr = pubkey_to_p2pkh(pub, info.p2pkh_version)
    wif = priv_to_wif(priv, info.wif_version, True)
    return addr, wif


def _eth_like(priv: bytes) -> Tuple[str, str]:
    """Return ``(address, hex_priv)`` for ETH style coins."""
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    pub = sk.get_verifying_key().to_string()
    addr = keccak(pub)[-20:]
    return to_checksum_address("0x" + addr.hex()), priv.hex()


def encode_privkey(coin: str, priv: bytes) -> Tuple[str, str]:
    """Encode ``priv`` for ``coin`` returning ``(address, wif_or_hex)``."""
    coin = coin.lower()
    if coin in {"eth", "pep"}:
        return _eth_like(priv)
    return _p2pkh_coin(priv, coin)


# Convenience wrappers preserved for backwards compatibility -----------------


def priv_to_btc(priv: bytes) -> Tuple[str, str]:
    return encode_privkey("btc", priv)


def priv_to_eth(priv: bytes) -> str:
    return encode_privkey("eth", priv)[0]
