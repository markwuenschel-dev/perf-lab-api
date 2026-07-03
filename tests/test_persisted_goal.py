"""Async DB tests for the athlete_profiles.primary_goal round-trip and its
effect on /next-session prescription.

Requires a live PostgreSQL instance (uses the async_db / http_client fixtures
from conftest.py) -- these SKIP gracefully when no test DB is reachable
(no Postgres service locally or in CI). See tests/test_goal_resolution.py for
a non-DB unit test of the goal-resolution ordering that always runs.
"""
import pytest
from httpx import AsyncClient

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


async def test_onboard_persists_primary_goal(http_client: AsyncClient):
    """POST /v1/onboard with goal="Powerlifting" persists onto the profile."""
    headers = await _register_and_token(http_client, "goal_onboard@test.com")

    onboard = await http_client.post(
        "/v1/onboard",
        json={
            "experience_level": "intermediate",
            "experience_years": 3.0,
            "available_days_per_week": 4,
            "equipment": ["barbell"],
            "goal": "Powerlifting",
        },
        headers=headers,
    )
    assert onboard.status_code == 200, onboard.text

    get = await http_client.get("/v1/profile", headers=headers)
    assert get.status_code == 200, get.text
    assert get.json()["primary_goal"] == "Powerlifting"


async def test_patch_profile_primary_goal_roundtrip(http_client: AsyncClient):
    """PATCH /v1/profile {"primary_goal": "Running"} then GET reflects it."""
    headers = await _register_and_token(http_client, "goal_patch@test.com")

    patch = await http_client.patch(
        "/v1/profile", json={"primary_goal": "Running"}, headers=headers
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["primary_goal"] == "Running"

    get = await http_client.get("/v1/profile", headers=headers)
    assert get.status_code == 200, get.text
    assert get.json()["primary_goal"] == "Running"


async def test_next_session_uses_persisted_goal_with_no_query_param(
    http_client: AsyncClient,
):
    """
    With a persisted primary_goal and no active block, GET /next-session with
    NO goal query param must prescribe for the stored goal, not the hardcoded
    "Strength" default. Mirrors the SBD assertion in
    test_prescriber_exercise_selection.py::test_powerlifting_prescription_returns_sbd_not_bodyweight.
    """
    headers = await _register_and_token(http_client, "goal_next_session@test.com")

    onboard = await http_client.post(
        "/v1/onboard",
        json={
            "experience_level": "intermediate",
            "experience_years": 3.0,
            "available_days_per_week": 4,
            "equipment": [],
            "goal": "Powerlifting",
        },
        headers=headers,
    )
    assert onboard.status_code == 200, onboard.text

    resp = await http_client.get("/v1/next-session", headers=headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    names = [e["name"] for e in data["exercises"]]
    joined = " ".join(names)
    assert any(m in joined for m in ("Squat", "Bench", "Deadlift")), names
    assert "Push-Up" not in names
    assert "Split Squat" not in names
    assert "Tempo Squat" not in names
