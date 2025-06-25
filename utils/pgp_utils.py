import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP

def encrypt_with_pgp(data: dict, public_key_path: str) -> str:
    """
    Encrypts a dictionary of match data with a PGP public key file.
    Returns a base64-encoded encrypted string.
    """
    try:
        with open(public_key_path, "rb") as key_file:
            public_key = RSA.import_key(key_file.read())
        cipher = PKCS1_OAEP.new(public_key)
        serialized_data = str(data).encode("utf-8")
        encrypted = cipher.encrypt(serialized_data)
        return base64.b64encode(encrypted).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"PGP encryption failed: {e}")
