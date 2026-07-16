"""The register route must not disclose internal error detail (INT-A8).

``POST /auth/register`` is unauthenticated: anyone on the internet can drive it into
its failure branches. A debugging aid shipped that echoed
``f"Registration failed: {type(exc).__name__}: {str(exc)}"`` straight back to that
caller, handing out driver names, SQL text, table names and constraint names — the
exact opposite of the promise made by the global handler in ``app/main.py``
("no internal detail leaked").

These are pure unit tests: the route is called directly with a mocked session, so
the failure branches can be forced deterministically without a live database.
"""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.api.v1.auth import RegisterRequest, register

pytestmark = pytest.mark.asyncio


# Fragments that must never reach an unauthenticated client. Each is a real thing the
# old f-string could emit: the driver/ORM stack, the SQL, the schema, the constraint.
_MUST_NOT_LEAK = (
    "ProgrammingError",
    "IntegrityError",
    "OSError",
    "asyncpg",
    "psycopg",
    "sqlalchemy",
    "UndefinedColumn",
    "INSERT INTO",
    "athlete_profiles",
    "hashed_password",
    "uq_users_email",
    "DETAIL:",
    "Traceback",
    "(Background on this error",
)


def _assert_no_disclosure(detail: object) -> None:
    text = str(detail)
    leaked = [frag for frag in _MUST_NOT_LEAK if frag.lower() in text.lower()]
    assert not leaked, f"response detail leaked internal detail {leaked!r}: {text!r}"


def _body() -> RegisterRequest:
    return RegisterRequest(email="new@example.com", password="password123")


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # Session.add is sync; keep it off the await path
    return db


def _driver_error() -> ProgrammingError:
    """A realistic driver-shaped failure: SQL text + table + column in the message."""
    return ProgrammingError(
        'INSERT INTO athlete_profiles (user_id) VALUES ($1)',
        {},
        Exception('column "hashed_password" of relation "users" does not exist'),
    )


async def test_unexpected_failure_does_not_disclose_internals():
    """The catch-all branch must return a generic message, not the exception text."""
    db = _mock_db()
    db.flush.side_effect = _driver_error()

    with patch("app.api.v1.auth.UserRepository") as repo:
        repo.return_value.get_by_email = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await register(body=_body(), db=db)

    assert exc.value.status_code == 500
    _assert_no_disclosure(exc.value.detail)
    db.rollback.assert_awaited_once()


async def test_unexpected_failure_is_logged_server_side(caplog):
    """The detail the client no longer sees must still reach the server log."""
    db = _mock_db()
    db.flush.side_effect = _driver_error()

    with caplog.at_level(logging.ERROR, logger="perflab"), \
            patch("app.api.v1.auth.UserRepository") as repo:
        repo.return_value.get_by_email = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await register(body=_body(), db=db)

    assert caplog.records, "the swallowed exception was not logged — 500s must stay diagnosable"
    assert any(r.exc_info for r in caplog.records), "log must carry the traceback (exc_info)"


async def test_duplicate_email_precheck_is_409_not_500():
    """The useful contract survives: a known-duplicate email is still a clean 409."""
    db = _mock_db()

    with patch("app.api.v1.auth.UserRepository") as repo:
        repo.return_value.get_by_email = AsyncMock(return_value=MagicMock())
        with pytest.raises(HTTPException) as exc:
            await register(body=_body(), db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Email already registered"
    _assert_no_disclosure(exc.value.detail)


async def test_duplicate_email_race_is_409_without_constraint_detail():
    """A unique-violation lost to a race is a client error, and names no constraint."""
    db = _mock_db()
    db.commit.side_effect = IntegrityError(
        "INSERT INTO users (email, hashed_password) VALUES ($1, $2)",
        {},
        Exception('duplicate key value violates unique constraint "uq_users_email"'),
    )

    with patch("app.api.v1.auth.UserRepository") as repo:
        repo.return_value.get_by_email = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await register(body=_body(), db=db)

    assert exc.value.status_code == 409
    _assert_no_disclosure(exc.value.detail)
    db.rollback.assert_awaited_once()
