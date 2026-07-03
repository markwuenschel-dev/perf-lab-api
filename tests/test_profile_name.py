"""Async DB tests for the athlete_profiles.display_name round-trip.

Requires a live PostgreSQL instance (uses async_db fixture from conftest.py).
Modeled on tests/test_integration_flow.py's HTTP-via-ASGITransport pattern.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_patch_profile_display_name_roundtrip(async_db):
    """
    PATCH /v1/profile {"display_name": "Mark"} then GET /v1/profile must
    return display_name == "Mark".
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_db
    from app.main import app

    async def _override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = _override_get_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/auth/register",
                json={"email": "display_name_patch@test.com", "password": "securepass1"},
            )
            assert reg.status_code == 201, reg.text

            tok = await client.post(
                "/auth/token",
                data={"username": "display_name_patch@test.com", "password": "securepass1"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert tok.status_code == 200, tok.text
            token = tok.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            patch = await client.patch(
                "/v1/profile", json={"display_name": "Mark"}, headers=headers
            )
            assert patch.status_code == 200, patch.text
            assert patch.json()["display_name"] == "Mark"

            get = await client.get("/v1/profile", headers=headers)
            assert get.status_code == 200, get.text
            assert get.json()["display_name"] == "Mark"
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_onboard_persists_display_name(async_db):
    """
    POST /v1/onboard with display_name must persist onto the AthleteProfile row.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select as sa_select

    from app.core.db import get_db
    from app.main import app
    from app.models.user import AthleteProfile

    async def _override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = _override_get_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/auth/register",
                json={"email": "display_name_onboard@test.com", "password": "securepass1"},
            )
            assert reg.status_code == 201, reg.text

            tok = await client.post(
                "/auth/token",
                data={"username": "display_name_onboard@test.com", "password": "securepass1"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert tok.status_code == 200, tok.text
            token = tok.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            onboard = await client.post(
                "/v1/onboard",
                json={
                    "display_name": "Ada Lovelace",
                    "experience_level": "intermediate",
                    "experience_years": 3.0,
                    "available_days_per_week": 4,
                    "equipment": ["barbell"],
                },
                headers=headers,
            )
            assert onboard.status_code == 200, onboard.text
            profile_id = onboard.json()["profile_id"]

        result = await async_db.execute(
            sa_select(AthleteProfile).where(AthleteProfile.id == profile_id)
        )
        profile = result.scalar_one()
        assert profile.display_name == "Ada Lovelace", (
            f"display_name expected 'Ada Lovelace', got {profile.display_name}"
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
