"""Route contract test for POST /v1/simulate/projection (requires a live DB).

Register -> project a Powerlifting plan and a Running plan, asserting the frozen
response shape and goal-specific axis growth. Mirrors the http_client + real-auth
pattern in tests/test_objectives_routes.py (SKIPs locally/CI without a DB).
"""

import pytest

from app.domain.vectors import CapacityState

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client, email: str, password: str) -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


def _axis(body, key):
    return next(a for a in body["axes"] if a["key"] == key)


async def test_projection_returns_frozen_contract(http_client):
    token = await _register_and_get_token(http_client, "proj_shape@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    resp = await http_client.post(
        "/v1/simulate/projection",
        json={
            "goal": "Powerlifting",
            "weeks": 8,
            "weekly_volume": 70,
            "intensity": "hard",
            "recovery": "standard",
        },
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["goal"] == "Powerlifting"
    assert body["weeks"] == 8
    assert [a["key"] for a in body["axes"]] == list(CapacityState.KEYS)
    for axis in body["axes"]:
        assert set(axis) == {
            "key", "label", "start", "projected", "baseline", "series", "baseline_series",
        }
        assert len(axis["series"]) == 9  # weeks + 1
        assert len(axis["baseline_series"]) == 9
    assert len(body["readiness_series"]) == 9
    assert 0.0 <= body["peak_fatigue"] <= 100.0


async def test_projection_is_goal_specific(http_client):
    token = await _register_and_get_token(http_client, "proj_goal@test.com", "securepass1")
    hdr = {"Authorization": f"Bearer {token}"}

    # Onboard with a modest squat 1RM so max_strength seeds below the axis ceiling
    # (the default intermediate seed pins it at 100 = no headroom to grow).
    onb = await http_client.post(
        "/v1/onboard",
        json={"experience_level": "beginner", "goal": "Powerlifting", "squat_1rm_kg": 70.0},
        headers=hdr,
    )
    assert onb.status_code == 200, onb.text

    lift = (
        await http_client.post(
            "/v1/simulate/projection",
            json={
                "goal": "Powerlifting",
                "weeks": 10,
                "weekly_volume": 85,
                "intensity": "hard",
                "recovery": "standard",
            },
            headers=hdr,
        )
    ).json()
    run = (
        await http_client.post(
            "/v1/simulate/projection",
            json={
                "goal": "Running",
                "weeks": 10,
                "weekly_volume": 80,
                "intensity": "balanced",
                "recovery": "standard",
            },
            headers=hdr,
        )
    ).json()

    # Powerlifting grows max_strength above its maintain baseline.
    lift_ms = _axis(lift, "max_strength")
    assert lift_ms["projected"] > lift_ms["baseline"]
    # Running grows aerobic above its maintain baseline.
    run_aero = _axis(run, "aerobic")
    assert run_aero["projected"] > run_aero["baseline"]
    # And the two plans steer growth to different axes.
    assert _axis(run, "aerobic")["projected"] > _axis(lift, "aerobic")["projected"]
    assert _axis(lift, "max_strength")["projected"] > _axis(run, "max_strength")["projected"]


async def test_projection_unauthenticated(http_client):
    resp = await http_client.post(
        "/v1/simulate/projection",
        json={
            "goal": "Powerlifting",
            "weeks": 8,
            "weekly_volume": 70,
            "intensity": "hard",
            "recovery": "standard",
        },
    )
    assert resp.status_code == 401
