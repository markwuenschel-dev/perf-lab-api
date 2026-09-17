"""The wire shape of every 204 DELETE (requires a live DB).

The server half of one seam. The web client decides how to read a reply from its
`content-type`; FastAPI builds a 204 with its default JSON response class, so these routes
answer with an EMPTY body that is still labelled `application/json`. Calling `.json()` on
that threw "Unexpected end of JSON input" and surfaced as a JSON error on every delete —
after the row was already gone. The client side is pinned in
`web/src/api/emptyResponse.test.ts`; this file pins what the client is reading.

Existing route tests assert `status_code == 204` only, which is exactly what let the defect
through: the status was never the part that broke.
"""
import pytest

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


def _no_content(resp) -> tuple[int, bytes]:
    """The pair the client must survive: the status, and the body it would try to parse.

    Returned rather than asserted in here so each test states the contract in its own body
    (tests/test_all_tests_assert_something.py requires that). Deliberately NOT asserting the
    absence of a JSON content-type: FastAPI sets one today, and the client is fixed to cope.
    """
    return resp.status_code, resp.content


async def _create_objective(client, hdr, label: str) -> dict:
    resp = await client.post("/v1/objectives", json={"label": label, "priority": 1}, headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_delete_objective_returns_empty_204(http_client):
    hdr = {
        "Authorization": f"Bearer {await _register_and_get_token(http_client, 'del_obj@test.com', 'securepass1')}"
    }
    objective = await _create_objective(http_client, hdr, "Sub-20 5k")

    status, body = _no_content(await http_client.delete(f"/v1/objectives/{objective['id']}", headers=hdr))

    assert status == 204
    assert body == b"", f"204 carried a body: {body!r}"


async def test_delete_macrocycle_returns_empty_204(http_client):
    hdr = {
        "Authorization": f"Bearer {await _register_and_get_token(http_client, 'del_macro@test.com', 'securepass1')}"
    }
    objective = await _create_objective(http_client, hdr, "Nationals")
    created = await http_client.post(
        "/v1/macrocycles", json={"objective_id": objective["id"]}, headers=hdr
    )
    assert created.status_code == 200, created.text

    status, body = _no_content(
        await http_client.delete(f"/v1/macrocycles/{created.json()['id']}", headers=hdr)
    )

    assert status == 204
    assert body == b"", f"204 carried a body: {body!r}"


async def test_disconnect_oura_without_connection_is_a_json_404(http_client):
    """The error path stays JSON — the client still parses `detail` from it."""
    hdr = {
        "Authorization": f"Bearer {await _register_and_get_token(http_client, 'del_oura@test.com', 'securepass1')}"
    }
    resp = await http_client.delete("/v1/integrations/oura/connection", headers=hdr)

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No Oura connection"
