"""Fernet at-rest encryption for wearable tokens (pure; no DB)."""
import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import settings


@pytest.fixture
def enc_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", key)
    crypto._fernet.cache_clear()
    yield key
    crypto._fernet.cache_clear()


def test_encrypt_decrypt_round_trip(enc_key):
    secret = "oura-access-token-abc123"
    ciphertext = crypto.encrypt(secret)
    assert ciphertext != secret  # actually encrypted
    assert crypto.decrypt(ciphertext) == secret


def test_ciphertext_is_nondeterministic(enc_key):
    # Fernet embeds a random IV, so the same plaintext encrypts differently.
    assert crypto.encrypt("x") != crypto.encrypt("x")


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", "")
    crypto._fernet.cache_clear()
    try:
        with pytest.raises(crypto.EncryptionKeyMissingError):
            crypto.encrypt("secret")
    finally:
        crypto._fernet.cache_clear()


def test_malformed_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    crypto._fernet.cache_clear()
    try:
        with pytest.raises(crypto.EncryptionKeyMissingError):
            crypto.encrypt("secret")
    finally:
        crypto._fernet.cache_clear()
