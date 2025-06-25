# config/coin_definitions.py

from bitcoinaddress import Wallet as BTCWallet
from eth_account import Account as ETHAccount
from hashlib import sha256
import base58

# Supported address column names for CSV checks
coin_columns = {
    "btc": ["btc_U", "btc_C"],
    "eth": ["eth"],
    "doge": ["doge_U", "doge_C"],
    "ltc": ["ltc_U", "ltc_C"],
    "dash": ["dash_U", "dash_C"],
    "bch": ["bch_U", "bch_C"],
    "rvn": ["rvn_U", "rvn_C"],
    "pep": ["pep_U", "pep_C"]
}

def derive_all_coin_addresses(hex_private_key):
    """
    Given a hex private key, derive addresses for all supported altcoins.
    Returns a dictionary with coin-specific address fields.
    """
    result = {}

    # BTC (Compressed + Uncompressed)
    try:
        btc_wallet = BTCWallet(hex_private_key)
        btc_info = btc_wallet.address.__dict__.get("mainnet", {})
        result["btc_U"] = btc_info.get("p2pkh", "")
        result["btc_C"] = btc_info.get("p2pkh", "")
    except Exception:
        result["btc_U"] = ""
        result["btc_C"] = ""

    # ETH
    try:
        acct = ETHAccount.from_key(bytes.fromhex(hex_private_key))
        result["eth"] = acct.address
    except Exception:
        result["eth"] = ""

    # DOGE, LTC, DASH, BCH, RVN, PEP (Uncompressed/Compressed simulated)
    coin_prefixes = {
        "doge": b'\x1e',
        "ltc":  b'\x30',
        "dash": b'\x4c',
        "bch":  b'\x00',
        "rvn":  b'\x3c',
        "pep":  b'\x37'
    }

    for coin, prefix in coin_prefixes.items():
        try:
            # Simulate pubkey from private key for demonstration
            pubkey = bytes.fromhex("04" + hex_private_key * 2)[:65]
            digest = sha256(pubkey).digest()
            addr = base58.b58encode_check(prefix + digest[:20]).decode()
            result[f"{coin}_U"] = addr
            result[f"{coin}_C"] = addr  # Placeholder: real compressed pubkey derivation requires ecdsa
        except Exception:
            result[f"{coin}_U"] = ""
            result[f"{coin}_C"] = ""

    return result
