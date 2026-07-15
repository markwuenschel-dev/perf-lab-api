"""
Write-boundary contract tests for /v1/profile.

Two defects share this boundary, so they share a test file:

INT-A5 — ``untracked_wellness_signals`` was accepted unvalidated. The readiness
engine intersects unknown names away
(``readiness_service`` → ``set(raw or []) & set(coverage_signals())``) while the
profile endpoint echoes the stored raw list back, so the API reported an opt-out
the engine never honoured. An unknown signal must be *rejected* at the boundary,
not accepted-then-ignored.

INT-A6 — the PATCH column map (``app.api.v1.profile._COLUMN_MAP``) is applied via
``setattr``. An unmapped key sets a plain Python attribute on the declarative
instance, which the following ``db.refresh`` discards — silently, with a 200. The
structural test below pins every mapping to a real mapped column so that drift
fails here instead of vanishing at runtime.
"""

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.api.v1.profile import _COLUMN_MAP
from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic.wellness_registry import coverage_signals
from app.main import app
from app.models.user import AthleteProfile, User
from app.schemas.profile import ProfileUpdate

# Applied per-test rather than module-wide: the structural INT-A6 checks and the
# schema-level INT-A5 checks are synchronous and need no event loop.
_asyncio = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mk_user(db, email: str) -> User:
    u = User(email=email, hashed_password="hashed", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _client_for(db, user):
    async def _override_db():
        yield db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# INT-A5 — untracked_wellness_signals is validated at the write boundary
# ---------------------------------------------------------------------------


@_asyncio
async def test_patch_rejects_unknown_untracked_wellness_signal(async_db):
    """An unregistered signal name is a 422, not a silently-ignored opt-out.

    Before the fix this returned 200 and echoed the bogus name straight back,
    while the readiness engine intersected it away — the API lied about state.
    """
    user = await _mk_user(async_db, email="profile-a5-unknown@test.com")
    try:
        async with _client_for(async_db, user) as client:
            resp = await client.patch(
                "/v1/profile",
                json={"untracked_wellness_signals": ["sleep", "not_a_real_signal"]},
            )
        assert resp.status_code == 422, resp.text
        assert "not_a_real_signal" in resp.text
    finally:
        app.dependency_overrides.clear()


@_asyncio
async def test_patch_accepts_every_registered_coverage_signal(async_db):
    """The whole registered coverage set round-trips — the validator must not over-reject."""
    user = await _mk_user(async_db, email="profile-a5-valid@test.com")
    signals = list(coverage_signals())
    try:
        async with _client_for(async_db, user) as client:
            resp = await client.patch(
                "/v1/profile",
                json={"untracked_wellness_signals": signals},
            )
        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["untracked_wellness_signals"]) == sorted(signals)
    finally:
        app.dependency_overrides.clear()


@_asyncio
async def test_patch_accepts_empty_untracked_wellness_signals(async_db):
    """An empty list clears the opt-out and stays a 200 (currently-working request)."""
    user = await _mk_user(async_db, email="profile-a5-empty@test.com")
    try:
        async with _client_for(async_db, user) as client:
            resp = await client.patch("/v1/profile", json={"untracked_wellness_signals": []})
        assert resp.status_code == 200, resp.text
        assert resp.json()["untracked_wellness_signals"] == []
    finally:
        app.dependency_overrides.clear()


def test_schema_rejects_unknown_signal_directly():
    """The rejection lives in the schema, so it holds for every caller of ProfileUpdate."""
    with pytest.raises(ValueError, match="not_a_real_signal"):
        ProfileUpdate(untracked_wellness_signals=["not_a_real_signal"])


def test_schema_allows_untracked_wellness_signals_to_be_omitted_or_null():
    """PATCH semantics: omitted stays unset; explicit null still parses."""
    assert "untracked_wellness_signals" not in ProfileUpdate().model_fields_set
    assert ProfileUpdate(untracked_wellness_signals=None).untracked_wellness_signals is None


# ---------------------------------------------------------------------------
# INT-A6 — the PATCH column map resolves to real columns
# ---------------------------------------------------------------------------


def _unresolvable_fields(column_map: dict[str, str]) -> list[str]:
    """API fields that ``setattr`` would write to a non-column attribute.

    Mirrors the endpoint's own resolution (``_COLUMN_MAP.get(key, key)``). Any
    name returned here would be set as a plain instance attribute and then thrown
    away by ``db.refresh`` — a silent 200-with-no-write.
    """
    columns = set(sa.inspect(AthleteProfile).columns.keys())
    return [
        field for field in ProfileUpdate.model_fields if column_map.get(field, field) not in columns
    ]


def test_column_map_targets_are_real_columns():
    """Every _COLUMN_MAP value names a mapped column on AthleteProfile."""
    columns = set(sa.inspect(AthleteProfile).columns.keys())
    bad = {api: col for api, col in _COLUMN_MAP.items() if col not in columns}
    assert bad == {}, f"_COLUMN_MAP targets that are not AthleteProfile columns: {bad}"


def test_column_map_keys_are_real_update_fields():
    """Every _COLUMN_MAP key is an actual ProfileUpdate field (no dead entries)."""
    stale = set(_COLUMN_MAP) - set(ProfileUpdate.model_fields)
    assert stale == set(), f"_COLUMN_MAP keys with no matching ProfileUpdate field: {stale}"


def test_every_update_field_resolves_to_a_column():
    """No ProfileUpdate field can be silently dropped by the PATCH setattr loop."""
    unresolvable = _unresolvable_fields(_COLUMN_MAP)
    assert unresolvable == [], (
        "ProfileUpdate fields that PATCH /v1/profile would silently drop "
        f"(setattr to a non-column, discarded by db.refresh): {unresolvable}"
    )


def test_resolution_guard_detects_a_drifted_map():
    """The guard above must actually catch drift — prove it fails on a bad map."""
    drifted = {**_COLUMN_MAP, "squat_1rm_kg": "squat_one_rep_max_typo"}
    assert _unresolvable_fields(drifted) == ["squat_1rm_kg"]
