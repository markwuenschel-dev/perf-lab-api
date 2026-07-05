"""Route tests for POST /v1/simulate-dose and POST /v1/log-workout."""
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(UTC).isoformat()
_WORKOUT_BODY = {
    "timestamp": _NOW,
    "modality": "Strength",
    "duration_minutes": 60.0,
    "session_rpe": 7.5,
    "total_volume_load": 4000.0,
    "estimated_sets": 12,
    "sleep_quality": 7.0,
    "life_stress_inverse": 7.0,
}


async def _register_and_login(client, email: str, password: str = "testpass99") -> str:
    """Helper: register a user and return a Bearer token."""
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


# ── simulate-dose (public) ────────────────────────────────────────────────────

async def test_simulate_dose_no_auth_required(http_client):
    resp = await http_client.post("/v1/simulate-dose", json=_WORKOUT_BODY)
    assert resp.status_code == 200


async def test_simulate_dose_returns_expected_fields(http_client):
    resp = await http_client.post("/v1/simulate-dose", json=_WORKOUT_BODY)
    data = resp.json()
    # simulate-dose returns a StressDose: the six dose axes live under `dose_six`,
    # alongside the derived systemic/peripheral scalars.
    assert "dose_six" in data
    for field in ("volume", "intensity", "density", "impact", "skill", "metabolic"):
        assert field in data["dose_six"], f"Missing dose_six field: {field}"
    for field in ("d_met_systemic", "d_nm_peripheral", "d_nm_central",
                  "d_struct_damage", "d_struct_signal"):
        assert field in data, f"Missing field: {field}"


async def test_simulate_dose_running_modality(http_client):
    body = dict(_WORKOUT_BODY, modality="Running", distance_meters=5000.0)
    resp = await http_client.post("/v1/simulate-dose", json=body)
    assert resp.status_code == 200
    assert resp.json()["dose_six"]["metabolic"] > 0


# ── log-workout (JWT protected) ───────────────────────────────────────────────

async def test_log_workout_without_auth_returns_401(http_client):
    resp = await http_client.post("/v1/log-workout", json=_WORKOUT_BODY)
    assert resp.status_code == 401


async def test_log_workout_returns_state_vector(http_client):
    token = await _register_and_login(http_client, "log_workout@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.post("/v1/log-workout", json=_WORKOUT_BODY, headers=headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    # UnifiedStateVector top-level fields
    for field in ("capacity_x", "fatigue_f", "tissue_t", "model_version"):
        assert field in data, f"Missing field in UnifiedStateVector response: {field}"


async def test_log_workout_fatigue_increases(http_client):
    """Logging a hard workout should produce nonzero fatigue channels."""
    token = await _register_and_login(http_client, "fatigue_check@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    body = dict(_WORKOUT_BODY, session_rpe=9.5, total_volume_load=8000.0)
    resp = await http_client.post("/v1/log-workout", json=body, headers=headers)
    assert resp.status_code == 200

    fatigue = resp.json()["fatigue_f"]
    total = sum(fatigue.get(k, 0) for k in ("cns", "muscular", "metabolic"))
    assert total > 0, "At least one fatigue channel should be positive after a hard workout"


async def test_log_workout_model_version_is_v03(http_client):
    token = await _register_and_login(http_client, "version_check@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.post("/v1/log-workout", json=_WORKOUT_BODY, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["model_version"] == "v0.3"


async def test_log_workout_invalid_rpe_returns_422(http_client):
    token = await _register_and_login(http_client, "invalid_rpe@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    body = dict(_WORKOUT_BODY, session_rpe=15.0)  # above max of 10
    resp = await http_client.post("/v1/log-workout", json=body, headers=headers)
    assert resp.status_code == 422
