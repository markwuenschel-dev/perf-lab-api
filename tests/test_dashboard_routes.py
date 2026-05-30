"""Route contract tests for /v1/dashboard/kpis, /v1/dashboard/domain-summary, /v1/dashboard/readiness."""
import pytest

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client, email: str, password: str) -> str:
    """Register a user and return a Bearer token string."""
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201, reg.text

    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def test_dashboard_kpis_authenticated(http_client):
    """GET /v1/dashboard/kpis with a valid Bearer token returns 200 with kpis and primary_anchors keys."""
    token = await _register_and_get_token(http_client, "dash_kpis@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/kpis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "kpis" in data
    assert "primary_anchors" in data
    assert isinstance(data["kpis"], list)
    assert isinstance(data["primary_anchors"], list)


async def test_dashboard_domain_summary_authenticated(http_client):
    """GET /v1/dashboard/domain-summary?domain=strength with a valid Bearer token returns 200 with expected keys."""
    token = await _register_and_get_token(http_client, "dash_domain@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/domain-summary",
        params={"domain": "strength"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "domain" in data
    assert "kpis" in data
    assert "primary_anchors" in data
    assert isinstance(data["domain"], str)
    assert isinstance(data["kpis"], list)
    assert isinstance(data["primary_anchors"], list)


async def test_dashboard_readiness_authenticated(http_client):
    """GET /v1/dashboard/readiness with a valid Bearer token returns 200 with state and kpi_flags keys."""
    token = await _register_and_get_token(http_client, "dash_readiness@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/readiness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "state" in data
    assert "kpi_flags" in data


async def test_dashboard_unauthenticated(http_client):
    """GET /v1/dashboard/kpis with no Authorization header returns 401."""
    resp = await http_client.get("/v1/dashboard/kpis")
    assert resp.status_code == 401, resp.text


async def test_dashboard_readiness_no_state_not_500(http_client):
    """GET /v1/dashboard/readiness for a fresh user (no AthleteState rows) returns 200 with state=null."""
    token = await _register_and_get_token(http_client, "dash_no_state@test.com", "securepass1")

    resp = await http_client.get(
        "/v1/dashboard/readiness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] is None
