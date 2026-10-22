"""INT-15 2C — display surfaces show recovered state WITH provenance, or refuse; never omit.

1. ``/v1/state-history``: a canonical row is ``canonical``; a row whose payload cannot be
   decoded but whose legacy mirror is usable is ``legacy_recovery`` with its reason; a row
   that cannot be shown even degraded refuses the WHOLE window with 409
   ``display_state_unavailable`` (never dropped: consumers read the last row as current).
   No history at all is still 200 [].
2. ``/v1/dashboard/readiness``: the same provenance on the single current state.
3. ``/v1/onboarding/state``: strict. A damaged row is ``state_available=false`` with no
   confidence band, distinct from absent state, nothing initialized over it, still 200.
4. None of these writes: every payload is byte-identical afterwards.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.engine.engine_state_codec import payload_hash
from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState

pytestmark = pytest.mark.asyncio

CANONICAL = default_engine_state_dict()

# Recoverable: the payload cannot be decoded, the legacy mirror is usable.
RECOVERABLE = [
    pytest.param(None, "null_engine_state_legacy_row", id="null-legacy-row"),
    pytest.param({"version": 2, "x": {}, "f": {}, "t": {}}, "vector_empty", id="empty-vectors"),
    pytest.param({"version": 2, "x": {"aerobic": 1.0}}, "missing_vectors", id="missing-vectors"),
    pytest.param(
        {"version": 2, "x": "not-an-object", "f": {}, "t": {}}, "vector_not_an_object",
        id="malformed",
    ),
]

# Not showable at all: a future version is never reconstructed; unusable scalars cannot be.
FUTURE = {"version": 99, "x": {}, "f": {}, "t": {}}


async def _athlete(client: AsyncClient, email: str) -> tuple[dict[str, str], int]:
    await client.post("/auth/register", json={"email": email, "password": "pw123456"})
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "pw123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    uid = (await client.get("/auth/me", headers=hdr)).json()["id"]
    return hdr, uid


async def _row(
    db: AsyncSession, user_id: int, payload: Any, *, day: int, aerobic: float = 300.0,
    fields: dict[str, Any] | None = None,
) -> AthleteState:
    row = AthleteState(
        user_id=user_id,
        timestamp=datetime(2026, 7, day, 12, 0),
        c_met_aerobic=aerobic, c_nm_force=1400.0, c_struct=100.0, b_met_anaerobic=15000.0,
        f_met_systemic=5.0, f_nm_peripheral=5.0, f_nm_central=5.0, f_struct_damage=5.0,
        engine_state=payload,
        **{"s_struct_signal": 0.0, "habit_strength": 0.5, "skill_state": {}, **(fields or {})},
    )
    db.add(row)
    await db.commit()
    return row


async def _payloads(user_id: int) -> list[tuple[int, str]]:
    """Committed payload fingerprints, through a separate connection (see test_c1)."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            rows = (await db.execute(
                select(AthleteState).where(AthleteState.user_id == user_id)
                .order_by(AthleteState.id)
            )).scalars().all()
            return [(r.id, payload_hash(r.engine_state)) for r in rows]
    finally:
        await engine.dispose()


# --- history -----------------------------------------------------------------------------


async def test_history_marks_each_row_with_its_provenance(
    http_client: AsyncClient, async_db: AsyncSession
) -> None:
    hdr, uid = await _athlete(http_client, "c2-hist-prov@t.io")
    await _row(async_db, uid, CANONICAL, day=1)
    await _row(async_db, uid, None, day=2)
    before = await _payloads(uid)

    res = await http_client.get("/v1/state-history", headers=hdr)

    assert res.status_code == 200
    rows = res.json()
    assert [(r["state_source"], r["degraded"], r["degradation_reason"]) for r in rows] == [
        ("canonical", False, None),
        ("legacy_recovery", True, "null_engine_state_legacy_row"),
    ]
    assert all(r["codec_version"] == "v2" for r in rows)
    assert await _payloads(uid) == before


@pytest.mark.parametrize(("payload", "reason"), RECOVERABLE)
async def test_a_recoverable_head_is_shown_degraded_never_as_canonical(
    http_client: AsyncClient, async_db: AsyncSession, payload: Any, reason: str
) -> None:
    hdr, uid = await _athlete(http_client, f"c2-hist-{reason}@t.io")
    await _row(async_db, uid, CANONICAL, day=1)
    await _row(async_db, uid, payload, day=2)
    before = await _payloads(uid)

    res = await http_client.get("/v1/state-history", headers=hdr)

    assert res.status_code == 200
    head = res.json()[-1]
    assert (head["state_source"], head["degraded"], head["degradation_reason"]) == (
        "legacy_recovery", True, reason,
    )
    assert await _payloads(uid) == before


@pytest.mark.parametrize(
    ("payload", "aerobic", "position"),
    [
        pytest.param(FUTURE, 300.0, "head", id="future-version-head"),
        pytest.param(None, float("nan"), "head", id="unusable-scalars-head"),
        pytest.param(FUTURE, 300.0, "middle", id="future-version-middle"),
    ],
)
async def test_an_unshowable_row_refuses_the_whole_window_never_drops_it(
    http_client: AsyncClient, async_db: AsyncSession, payload: Any, aerobic: float,
    position: str,
) -> None:
    """Dropping the head would make the previous snapshot look current; dropping a middle
    row would silently gap the chart. Both refuse explicitly instead."""
    hdr, uid = await _athlete(http_client, f"c2-hist-refuse-{position}-{aerobic}@t.io")
    await _row(async_db, uid, CANONICAL, day=1)
    await _row(async_db, uid, payload, day=2, aerobic=aerobic)
    if position == "middle":
        await _row(async_db, uid, CANONICAL, day=3)
    before = await _payloads(uid)

    res = await http_client.get("/v1/state-history", headers=hdr)

    assert res.status_code == 409
    assert res.json() == {
        "error": "display_state_unavailable",
        "history_available": False,
        "resolution_available_in_product": False,
    }
    assert await _payloads(uid) == before


async def test_no_history_is_an_empty_list_not_unavailable(
    http_client: AsyncClient,
) -> None:
    hdr, _ = await _athlete(http_client, "c2-hist-empty@t.io")
    res = await http_client.get("/v1/state-history", headers=hdr)
    assert (res.status_code, res.json()) == (200, [])


# --- dashboard readiness -------------------------------------------------------------------


async def test_dashboard_readiness_carries_provenance(
    http_client: AsyncClient, async_db: AsyncSession
) -> None:
    hdr, uid = await _athlete(http_client, "c2-dash@t.io")
    await _row(async_db, uid, None, day=1)
    body = (await http_client.get("/v1/dashboard/readiness", headers=hdr)).json()
    assert (body["state_source"], body["degraded"], body["degradation_reason"]) == (
        "legacy_recovery", True, "null_engine_state_legacy_row",
    )
    assert body["codec_version"] == "v2"


async def test_dashboard_readiness_refuses_an_unshowable_head(
    http_client: AsyncClient, async_db: AsyncSession
) -> None:
    hdr, uid = await _athlete(http_client, "c2-dash-future@t.io")
    await _row(async_db, uid, FUTURE, day=1)
    res = await http_client.get("/v1/dashboard/readiness", headers=hdr)
    assert res.status_code == 409
    assert res.json()["dashboard_state_available"] is False


async def test_dashboard_readiness_without_state_has_no_provenance(
    http_client: AsyncClient,
) -> None:
    hdr, _ = await _athlete(http_client, "c2-dash-none@t.io")
    body = (await http_client.get("/v1/dashboard/readiness", headers=hdr)).json()
    assert body["state"] is None
    assert (body["state_source"], body["degraded"], body["codec_version"]) == (None, None, None)


# --- onboarding twin summary ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "payload"),
    [
        pytest.param(0, None, id="null-legacy-row"),
        pytest.param(1, {"version": 2, "x": {}, "f": {}, "t": {}}, id="empty-vectors"),
        pytest.param(2, {"version": 2, "x": "not-an-object", "f": {}, "t": {}}, id="malformed"),
        pytest.param(3, FUTURE, id="future-version"),
    ],
)
async def test_onboarding_over_damaged_state_is_unavailable_not_provisional(
    http_client: AsyncClient, async_db: AsyncSession, n: int, payload: Any
) -> None:
    hdr, uid = await _athlete(http_client, f"c2-onb-{n}@t.io")
    await _row(async_db, uid, payload, day=1)
    before = await _payloads(uid)

    res = await http_client.get("/v1/onboarding/state", headers=hdr)

    assert res.status_code == 200  # onboarding is never blocked
    twin = res.json()["twin"]
    assert (twin["seeded"], twin["state_available"], twin["overall_confidence"]) == (
        True, False, None,
    )
    # Never initialized over an existing row, and the row itself untouched.
    assert await _payloads(uid) == before


async def test_onboarding_absent_state_is_distinct_from_damaged(
    http_client: AsyncClient,
) -> None:
    hdr, uid = await _athlete(http_client, "c2-onb-absent@t.io")
    twin = (await http_client.get("/v1/onboarding/state", headers=hdr)).json()["twin"]
    assert (twin["seeded"], twin["state_available"]) == (False, False)
    assert await _payloads(uid) == []  # reading onboarding state seeds nothing


async def test_onboarding_canonical_state_is_available(
    http_client: AsyncClient, async_db: AsyncSession
) -> None:
    hdr, uid = await _athlete(http_client, "c2-onb-ok@t.io")
    await _row(async_db, uid, CANONICAL, day=1)
    twin = (await http_client.get("/v1/onboarding/state", headers=hdr)).json()["twin"]
    assert (twin["seeded"], twin["state_available"]) == (True, True)
    assert twin["overall_confidence"] is not None


# --- review P1: a row whose own columns cannot form a state ---------------------------------
# The payload decodes (or the legacy mirror is usable), but habit/signal/skills are damaged:
# these have no lossy mirror to recover from, so every policy treats the row as damaged.

ROW_FIELD_DAMAGE = [
    pytest.param({"habit_strength": 2.0}, id="habit-above-one"),
    pytest.param({"s_struct_signal": -1.0}, id="negative-signal"),
    pytest.param({"skill_state": "oops"}, id="skill-state-not-an-object"),
]


def _tag(fields: dict[str, Any]) -> str:
    return next(iter(fields)).replace("_", "")


@pytest.mark.parametrize("fields", ROW_FIELD_DAMAGE)
@pytest.mark.parametrize("payload", [CANONICAL, None], ids=["canonical", "legacy-row"])
@pytest.mark.parametrize("position", ["head", "middle"])
async def test_damaged_row_fields_refuse_the_whole_history_window(
    http_client: AsyncClient, async_db: AsyncSession, fields: dict[str, Any], payload: Any,
    position: str,
) -> None:
    hdr, uid = await _athlete(
        http_client, f"c2-fields-hist-{_tag(fields)}-{payload is None}-{position}@t.io"
    )
    await _row(async_db, uid, CANONICAL, day=1)
    await _row(async_db, uid, payload, day=2, fields=fields)
    if position == "middle":
        await _row(async_db, uid, CANONICAL, day=3)
    before = await _payloads(uid)

    res = await http_client.get("/v1/state-history", headers=hdr)

    assert res.status_code == 409, res.text
    assert res.json()["error"] == "display_state_unavailable"
    assert await _payloads(uid) == before


@pytest.mark.parametrize("fields", ROW_FIELD_DAMAGE)
async def test_damaged_row_fields_refuse_dashboard_readiness(
    http_client: AsyncClient, async_db: AsyncSession, fields: dict[str, Any]
) -> None:
    hdr, uid = await _athlete(http_client, f"c2-fields-dash-{_tag(fields)}@t.io")
    await _row(async_db, uid, CANONICAL, day=1, fields=fields)

    res = await http_client.get("/v1/dashboard/readiness", headers=hdr)

    assert res.status_code == 409, res.text
    assert res.json()["dashboard_state_available"] is False


@pytest.mark.parametrize("fields", ROW_FIELD_DAMAGE)
async def test_damaged_row_fields_make_onboarding_unavailable_not_blocked(
    http_client: AsyncClient, async_db: AsyncSession, fields: dict[str, Any]
) -> None:
    hdr, uid = await _athlete(http_client, f"c2-fields-onb-{_tag(fields)}@t.io")
    await _row(async_db, uid, CANONICAL, day=1, fields=fields)
    before = await _payloads(uid)

    res = await http_client.get("/v1/onboarding/state", headers=hdr)

    assert res.status_code == 200, res.text
    twin = res.json()["twin"]
    assert (twin["seeded"], twin["state_available"], twin["overall_confidence"]) == (
        True, False, None,
    )
    assert await _payloads(uid) == before


@pytest.mark.parametrize("fields", ROW_FIELD_DAMAGE)
async def test_damaged_row_fields_refuse_a_decision(
    http_client: AsyncClient, async_db: AsyncSession, fields: dict[str, Any]
) -> None:
    """A decision route refuses with the typed 409, never a 500 from a validation error."""
    hdr, uid = await _athlete(http_client, f"c2-fields-dec-{_tag(fields)}@t.io")
    await _row(async_db, uid, CANONICAL, day=1, fields=fields)

    res = await http_client.get("/v1/readiness", headers=hdr)

    assert res.status_code == 409, res.text
    assert res.json()["error"] == "canonical_state_invalid"
