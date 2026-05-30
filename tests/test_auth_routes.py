"""Route contract tests for /auth/register, /auth/token, /auth/me."""
import pytest

pytestmark = pytest.mark.asyncio


async def test_register_success(http_client):
    """POST /auth/register with valid payload returns 201 with expected fields."""
    resp = await http_client.post(
        "/auth/register",
        json={"email": "register_success@test.com", "password": "securepass1"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "id" in data and isinstance(data["id"], int)
    assert "email" in data and isinstance(data["email"], str)
    assert "is_active" in data and isinstance(data["is_active"], bool)


async def test_register_duplicate_email(http_client):
    """Registering the same email twice returns 409 on the second call."""
    payload = {"email": "duplicate@test.com", "password": "securepass1"}
    first = await http_client.post("/auth/register", json=payload)
    assert first.status_code == 201, first.text

    second = await http_client.post("/auth/register", json=payload)
    assert second.status_code == 409, second.text


async def test_register_missing_field(http_client):
    """POST /auth/register without the password field returns 422."""
    resp = await http_client.post(
        "/auth/register",
        json={"email": "missing_field@test.com"},
    )
    assert resp.status_code == 422, resp.text


async def test_login_success(http_client):
    """Correct credentials to /auth/token return 200 with access_token."""
    email = "login_success@test.com"
    password = "securepass1"
    reg = await http_client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert reg.status_code == 201, reg.text

    resp = await http_client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


async def test_login_wrong_password(http_client):
    """Correct email, wrong password returns 401."""
    email = "wrong_pw@test.com"
    reg = await http_client.post(
        "/auth/register", json={"email": email, "password": "correctpass1"}
    )
    assert reg.status_code == 201, reg.text

    resp = await http_client.post(
        "/auth/token",
        data={"username": email, "password": "wrongpassword"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401, resp.text


async def test_login_nonexistent_user(http_client):
    """POST /auth/token for an email that was never registered returns 401."""
    resp = await http_client.post(
        "/auth/token",
        data={"username": "ghost@test.com", "password": "doesnotmatter"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401, resp.text


async def test_me_with_valid_token(http_client):
    """GET /auth/me with a valid Bearer token returns 200 with user fields."""
    email = "me_endpoint@test.com"
    password = "securepass1"
    reg = await http_client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert reg.status_code == 201, reg.text

    tok = await http_client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    token = tok.json()["access_token"]

    resp = await http_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data
    assert "email" in data
    assert "is_active" in data
