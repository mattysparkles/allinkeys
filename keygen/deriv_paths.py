"""Derivation path helpers for mnemonic mode.

This module defines wallet path presets and utilities for resolving
per-coin derivation paths. Only a subset of coins is fully implemented,
but the mapping structure is designed for easy extension.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Coin metadata
# ---------------------------------------------------------------------------

@dataclass
class CoinInfo:
    """Simple container for coin specific metadata."""

    slip44: int
    wif_version: int
    p2pkh_version: int


COIN_INFO: Dict[str, CoinInfo] = {
    # Currently implemented coins.  Additional coins can be appended here
    # with their SLIP-44 coin type and version byte information.
    "btc": CoinInfo(slip44=0, wif_version=0x80, p2pkh_version=0x00),
    "eth": CoinInfo(slip44=60, wif_version=0x00, p2pkh_version=0x00),
    # Placeholders for future coins
    "ltc": CoinInfo(slip44=2, wif_version=0xb0, p2pkh_version=0x30),
    "bch": CoinInfo(slip44=145, wif_version=0x80, p2pkh_version=0x00),
    "dash": CoinInfo(slip44=5, wif_version=0xcc, p2pkh_version=0x4c),
    "doge": CoinInfo(slip44=3, wif_version=0x9e, p2pkh_version=0x1e),
    "pep": CoinInfo(slip44=207, wif_version=0x37, p2pkh_version=0x37),
    "rvn": CoinInfo(slip44=175, wif_version=0x80, p2pkh_version=0x3c),
}

SUPPORTED_COINS = list(COIN_INFO.keys())

# ---------------------------------------------------------------------------
# Wallet path presets.  The values are per-coin derivation paths.  These are
# representative examples; they can be expanded or adjusted as project needs
# evolve.
# ---------------------------------------------------------------------------

WALLET_PRESETS: Dict[str, Dict[str, str]] = {
    "atomic": {
        "btc": "m/44'/0'/0'/0/0",
        "eth": "m/44'/60'/0'/0/0",
    },
    "coinomi": {
        "btc": "m/44'/0'/0'/0/0",
        "eth": "m/44'/60'/0'/0/0",
    },
    "ledger": {
        "btc": "m/84'/0'/0'/0/0",
        "eth": "m/44'/60'/0'/0/0",
    },
    "trust": {
        "btc": "m/84'/0'/0'/0/0",
        "eth": "m/44'/60'/0'/0/0",
    },
    "trezor": {
        "btc": "m/84'/0'/0'/0/0",
        "eth": "m/44'/60'/0'/0/0",
    },
}

DEFAULT_PATHS = {
    "btc": "m/44'/0'/0'/0/0",
    "eth": "m/44'/60'/0'/0/0",
}

# ---------------------------------------------------------------------------


def resolve_paths(
    coins: List[str],
    preset: Optional[str] = None,
    global_path: Optional[str] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Resolve per-coin derivation paths.

    Parameters
    ----------
    coins:
        Iterable of coin symbols (lower case) to resolve.
    preset:
        Optional wallet preset name.  If provided the preset is used as the
        base mapping.
    global_path:
        Optional path applied to all coins as a fallback when a preset is not
        supplied or when the preset does not define the coin.
    overrides:
        Mapping of ``coin -> path`` that overrides both presets and the global
        path.
    """

    result: Dict[str, str] = {}
    preset_paths = WALLET_PRESETS.get(preset, {}) if preset else {}
    overrides = overrides or {}

    for coin in coins:
        if coin in overrides and overrides[coin]:
            result[coin] = overrides[coin]
            continue
        if coin in preset_paths:
            result[coin] = preset_paths[coin]
            continue
        if global_path:
            result[coin] = global_path
            continue
        # Fallback to project defaults
        result[coin] = DEFAULT_PATHS.get(coin, "m/44'/0'/0'/0/0")
    return result
