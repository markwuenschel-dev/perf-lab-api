"""INT-01 — production boot refuses a default/empty/weak SECRET_KEY.

The guard signs every JWT (HS256); a default or trivially short key allows token
forgery. It fails fast in production and only warns elsewhere. DB-free.
"""
import pytest

from app.core.config import DEFAULT_SECRET_KEY, Settings
from app.main import MIN_SECRET_KEY_LENGTH, _check_production_secrets

_STRONG = "a" * MIN_SECRET_KEY_LENGTH  # 32 chars — at the floor


def test_production_default_key_raises():
    with pytest.raises(RuntimeError):
        _check_production_secrets(
            Settings(ENVIRONMENT="production", SECRET_KEY=DEFAULT_SECRET_KEY)
        )


def test_production_empty_key_raises():
    with pytest.raises(RuntimeError):
        _check_production_secrets(Settings(ENVIRONMENT="production", SECRET_KEY=""))


def test_production_whitespace_key_raises():
    with pytest.raises(RuntimeError):
        _check_production_secrets(Settings(ENVIRONMENT="production", SECRET_KEY="   "))


def test_production_short_key_raises():
    with pytest.raises(RuntimeError):
        _check_production_secrets(
            Settings(ENVIRONMENT="production", SECRET_KEY="x" * 8)
        )


def test_production_strong_key_passes():
    # A ≥32-char non-default key boots cleanly (no raise).
    _check_production_secrets(Settings(ENVIRONMENT="production", SECRET_KEY=_STRONG))


def test_development_default_key_does_not_raise():
    # Non-production with the default key must NOT raise (it warns, but never blocks
    # a local/dev boot). The load-bearing contract is "dev never fails fast".
    _check_production_secrets(
        Settings(ENVIRONMENT="development", SECRET_KEY=DEFAULT_SECRET_KEY)
    )


def test_development_short_key_does_not_raise():
    _check_production_secrets(Settings(ENVIRONMENT="development", SECRET_KEY="x" * 4))
