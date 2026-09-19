"""
Secret Box - Encryption at rest for values that must be recoverable in
plaintext server-side (unlike a password/API-key, which is only ever
compared by hash).

Used for the tenant HMAC secret (see TenantRepository): verifying a
request signature requires the raw secret as HMAC key material, so a
one-way hash - fine for the API key itself - won't work here. Keyed by
SECURITY_API_KEY_ENCRYPTION_KEY, which never leaves this process.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config.settings import settings


def _fernet() -> Fernet:
    # Fernet requires a 32-byte url-safe base64 key; SECURITY_API_KEY_ENCRYPTION_KEY
    # is an arbitrary-length secret (like the JWT key), so derive a valid Fernet
    # key from it rather than requiring operators to generate a second format.
    derived = hashlib.sha256(settings.security.api_key_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    """Encrypts a value for storage. Returns an opaque token safe to persist."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decrypts a value previously produced by encrypt_secret.

    Raises ValueError if the token is malformed or was encrypted under a
    different SECURITY_API_KEY_ENCRYPTION_KEY (e.g. after a key rotation
    that didn't re-encrypt existing rows).
    """
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Could not decrypt secret - wrong key or corrupted value") from e


__all__ = ["encrypt_secret", "decrypt_secret"]
