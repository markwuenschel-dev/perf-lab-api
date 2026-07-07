"""Symmetric encryption for secrets stored at rest (Phase 2 — wearable tokens).

Wearable OAuth/PAT tokens are long-lived credentials, so they are Fernet-encrypted
before hitting the database (``WearableConnection.access_token_enc`` etc.). Fernet
gives us authenticated encryption (AES-128-CBC + HMAC) with a single symmetric key.

The key lives in ``settings.APP_ENCRYPTION_KEY`` (a urlsafe-base64 32-byte value):

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Encryption is *opt-in*: the key may be unset in a dev environment that never touches
wearables. We therefore only require it lazily — ``encrypt``/``decrypt`` raise a clear
error if called without a key, so the rest of the app boots fine without one.
``cryptography`` is already available transitively via ``python-jose[cryptography]``.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionKeyMissingError(RuntimeError):
    """Raised when an encrypt/decrypt is attempted without APP_ENCRYPTION_KEY set."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.APP_ENCRYPTION_KEY.strip()
    if not key:
        raise EncryptionKeyMissingError(
            "APP_ENCRYPTION_KEY is not set. Generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and set it in the environment '
            "before using wearable sync."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:  # malformed key
        raise EncryptionKeyMissingError(
            "APP_ENCRYPTION_KEY is not a valid Fernet key (expected urlsafe-base64 "
            "32 bytes)."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns urlsafe-base64 ciphertext."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a stored secret. Raises ``InvalidToken`` on tamper/wrong key."""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


__all__ = ["EncryptionKeyMissingError", "InvalidToken", "decrypt", "encrypt"]
