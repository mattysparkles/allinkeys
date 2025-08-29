"""Utilities for Bitcoin puzzle mode.

Provides helper to compute start/end ranges and target address for a
given puzzle number. Puzzle ranges follow the pattern used by the
original Bitcoin puzzle challenge where puzzle ``n`` covers the private
key space ``[2^(n-1), 2^n - 1]`` and the published address corresponds to
``2^(n-1)``.
"""
from __future__ import annotations

import hashlib
from typing import Dict

import ecdsa
import base58


def _priv_to_p2pkh_uncompressed(value: int) -> str:
    priv_bytes = value.to_bytes(32, "big")
    sk = ecdsa.SigningKey.from_string(priv_bytes, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    pubkey = b"\x04" + vk.to_string()
    sha = hashlib.sha256(pubkey).digest()
    rip = hashlib.new("ripemd160", sha).digest()
    payload = b"\x00" + rip
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def get_puzzle_info(n: int) -> Dict[str, str]:
    """Return start/end range and address for puzzle ``n``.

    Parameters
    ----------
    n: int
        Puzzle number (1-based).
    """
    if n < 1:
        raise ValueError("Puzzle number must be positive")
    start = 1 << (n - 1)
    end = (1 << n) - 1
    address = _priv_to_p2pkh_uncompressed(start)
    return {
        "start": f"{start:064x}",
        "end": f"{end:064x}",
        "address": address,
    }
