"""OAuth ``state`` token sign/verify (pure; no DB).

The state token carries user identity through the browser redirect, so its
integrity is security-critical — wrong-purpose, expired, and tampered tokens must
all be rejected.
"""
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.config import settings
from app.services import wearable_service


def test_sign_verify_round_trip():
    tok = wearable_service.sign_state(42)
    assert wearable_service.verify_state(tok) == 42


def test_wrong_purpose_rejected():
    bad = jwt.encode(
        {"sub": "1", "purpose": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(ValueError):
        wearable_service.verify_state(bad)


def test_expired_rejected():
    expired = jwt.encode(
        {"sub": "1", "purpose": "oura_oauth", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(ValueError):
        wearable_service.verify_state(expired)


def test_tampered_rejected():
    tok = wearable_service.sign_state(7)
    with pytest.raises(ValueError):
        wearable_service.verify_state(tok + "tamper")


def test_wrong_secret_rejected():
    forged = jwt.encode(
        {"sub": "1", "purpose": "oura_oauth", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "not-the-app-secret",
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(ValueError):
        wearable_service.verify_state(forged)
