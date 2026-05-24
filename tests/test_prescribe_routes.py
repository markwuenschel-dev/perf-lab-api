"""Route tests for GET /v1/next-session."""
import pytest

pytestmark = pytest.mark.asyncio


async def _register_and_login(client, email: str, password: str = "testpass99") -> str:
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


# ── /v1/next-session ──────────────────────────────────────────────────────────

async def test_next_session_without_auth_returns_401(http_client):
    resp = await http_client.get("/v1/next-session")
    assert resp.status_code == 401


async def test_next_session_returns_prescription_shape(http_client):
    """Fresh user gets a valid prescription (auto-init of S0 triggers)."""
    token = await _register_and_login(http_client, "prescribe_shape@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session", headers=headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    for field in ("type", "focus", "rationale", "duration_min", "model_version", "exercises"):
        assert field in data, f"Missing field: {field}"


async def test_next_session_auto_inits_state(http_client):
    """Calling next-session without explicit state init should still succeed (auto-init)."""
    token = await _register_and_login(http_client, "auto_init@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=Strength", headers=headers)
    assert resp.status_code == 200


async def test_next_session_goal_hypertrophy(http_client):
    token = await _register_and_login(http_client, "hyper_goal@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=Hypertrophy", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] != ""


async def test_next_session_goal_power(http_client):
    token = await _register_and_login(http_client, "power_goal@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=Power", headers=headers)
    assert resp.status_code == 200


async def test_next_session_goal_general(http_client):
    token = await _register_and_login(http_client, "general_goal@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=General", headers=headers)
    assert resp.status_code == 200


async def test_next_session_why_field_present(http_client):
    """The `why` field (PrescriptionExplanation) should be populated."""
    token = await _register_and_login(http_client, "why_field@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session?goal=Strength", headers=headers)
    assert resp.status_code == 200

    why = resp.json().get("why")
    assert why is not None, "why field should be present in prescription"
    assert "state_drivers" in why
    assert "constraints_applied" in why


async def test_next_session_model_version_v03(http_client):
    token = await _register_and_login(http_client, "model_ver@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await http_client.get("/v1/next-session", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["model_version"] == "v0.3"
