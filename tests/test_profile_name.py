"""Async DB tests for the athlete_profiles.display_name round-trip.

Requires a live PostgreSQL instance (uses the async_db / http_client fixtures
from conftest.py). Uses the shared `http_client` fixture for consistency with
test_auth_routes.py / test_wellness_routes.py.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AthleteProfile

pytestmark = pytest.mark.asyncio


async def _register_and_token(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post(
        "/auth/register", json={"email": email, "password": "securepass1"}
    )
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def test_patch_profile_display_name_roundtrip(http_client: AsyncClient):
    """
    PATCH /v1/profile {"display_name": "Mark"} then GET /v1/profile must
    return display_name == "Mark".
    """
    headers = await _register_and_token(http_client, "display_name_patch@test.com")

    patch = await http_client.patch(
        "/v1/profile", json={"display_name": "Mark"}, headers=headers
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["display_name"] == "Mark"

    get = await http_client.get("/v1/profile", headers=headers)
    assert get.status_code == 200, get.text
    assert get.json()["display_name"] == "Mark"


async def test_onboard_persists_display_name(
    http_client: AsyncClient, async_db: AsyncSession
):
    """
    POST /v1/onboard with display_name must persist onto the AthleteProfile row.
    """
    headers = await _register_and_token(http_client, "display_name_onboard@test.com")

    onboard = await http_client.post(
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
        select(AthleteProfile).where(AthleteProfile.id == profile_id)
    )
    profile = result.scalar_one()
    assert profile.display_name == "Ada Lovelace", (
        f"display_name expected 'Ada Lovelace', got {profile.display_name}"
    )
