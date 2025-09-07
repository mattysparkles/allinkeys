# config/constants.py

# This is the order of the secp256k1 curve (used in Bitcoin)
SECP256K1_ORDER = int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16)

# SHA256 checksums for external download sources.
#
# NOTE: The values below are placeholders and should be replaced with the
# actual hashes of the referenced files.  Downloads will be rejected if the
# computed checksum does not match the expected value.
DOWNLOAD_SHA256 = {
    # Coin funded address lists
    "https://addresses.loyce.club/Bitcoin_addresses_LATEST.txt.gz": "0" * 64,
    "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dogecoin/Latest_Dogecoin_Addresses.tsv.gz": "0" * 64,
    "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Litecoin/Latest_Litecoin_Addresses.tsv.gz": "0" * 64,
    "https://raw.githubusercontent.com/Pymmdrza/Rich-Address-Wallet/refs/heads/main/ETHEREUM/EthRich.txt": "0" * 64,
    "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/BitcoinCash/Latest_BitcoinCash_Addresses.tsv.gz": "0" * 64,
    "https://github.com/Pymmdrza/Rich-Address-Wallet/releases/download/Dash/Latest_Dash_Addresses.tsv.gz": "0" * 64,
    # BTC address range file
    "https://alladdresses.loyce.club/all_Bitcoin_addresses_ever_used_sorted.txt.gz": "0" * 64,
}

