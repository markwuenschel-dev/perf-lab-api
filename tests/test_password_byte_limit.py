"""bcrypt's password limit is 72 BYTES, not 72 characters.

Registration validated the password with ``len(v) > 72`` — a character count — and so did
``hash_password``. A password of, say, 40 accented characters is only 40 *characters* but
80 *bytes* in UTF-8, so it slipped past both guards and reached ``bcrypt.hashpw``, which (on
bcrypt >= 4.1, and 5.x here) raises ``ValueError: password cannot be longer than 72 bytes``.
That raise landed in register's generic ``except Exception`` and came back as a 500 — a
server error for what is really invalid user input.

The fix counts bytes in both guards, so an over-long password is rejected as a 422 at the
request boundary, long before bcrypt sees it. (Requires a DB for the register round-trip.)
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_multibyte_password_over_72_bytes_is_a_422_not_a_500(http_client):
    """40 two-byte chars = 80 bytes: under the char limit, over the byte limit."""
    password = "é" * 40  # 40 characters, 80 UTF-8 bytes
    assert len(password) <= 72 and len(password.encode("utf-8")) > 72

    resp = await http_client.post(
        "/auth/register", json={"email": "bytelimit@test.com", "password": password}
    )
    assert resp.status_code == 422, (
        f"expected 422 for an over-72-byte password, got {resp.status_code}: {resp.text}"
    )


async def test_password_at_the_byte_boundary_still_registers(http_client):
    """A 72-byte ASCII password is exactly at the limit and must still be accepted."""
    password = "a" * 72  # 72 characters, 72 bytes
    resp = await http_client.post(
        "/auth/register", json={"email": "boundary@test.com", "password": password}
    )
    assert resp.status_code == 201, resp.text
