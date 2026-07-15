"""INT-15 W1-A slice 2B1 — prescription refuses to size training from untrusted state.

The first slice on this branch that changes production behaviour. An athlete whose
canonical state does not decode now gets an explicit refusal instead of a prescription
quietly sized from the lossy legacy scalar mirror.

The failure mapping under test has three distinct layers, and collapsing any two of them
is the defect::

    engine_state_codec     "this payload does not decode"      persistence
            │
            ▼   translated by the service that knows its authority
    CanonicalStateInvalid  "prescription must be refused"      product policy
            │
            ▼
    global handler         409 + canonical_state_invalid       transport

`test_untranslated_codec_error_is_a_500_defect_not_a_409` is the load-bearing one: a raw
codec error escaping to HTTP must stay an opaque 500. If it were mapped to a tidy 409,
every forgotten translation in 2B2/2B3/2B4 would look like a working refusal.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pytest

from app.core.errors import CanonicalStateInvalid, normalize_decode_error
from app.engine.engine_state_codec import (
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
)
from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState
from app.models.mesocycle import (
    BlockGoal,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.services import prescription_service

pytestmark = pytest.mark.asyncio

EMPTY_VECTORS = {"version": 2, "x": {}, "f": {}, "t": {}}


async def _register(client, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "pw123456"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "pw123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _user_id(client, hdr) -> int:
    me = await client.get("/auth/me", headers=hdr)
    return me.json()["id"]


async def _damaged_state(db, user_id: int, payload: object) -> AthleteState:
    """A state row whose engine_state is unreadable but whose legacy mirror is healthy.

    The healthy mirror is the point: a permissive loader WOULD succeed here and hand back
    a reconstruction. Every refusal below has that temptation sitting in the same row.
    """
    row = AthleteState(
        user_id=user_id,
        timestamp=datetime(2026, 7, 1, 12, 0),
        c_met_aerobic=300.0,
        c_nm_force=1400.0,
        c_struct=100.0,
        b_met_anaerobic=15000.0,
        f_met_systemic=5.0,
        f_nm_peripheral=5.0,
        f_nm_central=5.0,
        f_struct_damage=5.0,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        engine_state=payload,
    )
    db.add(row)
    await db.commit()
    return row


async def _state_count(db, user_id: int) -> int:
    from sqlalchemy import func, select

    res = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(res.scalar_one())


# ── the reason taxonomy ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (MissingEngineState("x"), "canonical_state_missing"),
        (MalformedCurrentEngineState("vector_empty"), "canonical_state_incomplete"),
        (MalformedCurrentEngineState("missing_vectors"), "canonical_state_incomplete"),
        (MalformedCurrentEngineState("vector_validation_failed"), "canonical_state_malformed"),
        (MalformedCurrentEngineState("unparseable_payload"), "canonical_state_malformed"),
        (UnsupportedFutureEngineStateVersion(99), "canonical_state_version_unsupported"),
    ],
)
def test_internal_reason_taxonomy_stays_precise(exc, expected) -> None:
    """Externally these collapse to one code; internally they must not.

    Future-version is the one that matters most: it is not damaged data, and its
    operational response is "deploy readers", not "repair the row".
    """
    assert normalize_decode_error(exc) == expected


def test_refusal_body_is_the_settled_external_contract() -> None:
    body = CanonicalStateInvalid(
        capability="prescription", normalized_reason="canonical_state_incomplete"
    ).to_response_body()

    assert body == {
        "error": "canonical_state_invalid",
        "prescription_available": False,
        "resolution_available_in_product": False,
    }
    # The internal reason is deliberately absent — it goes to logs, not to the client.
    assert "canonical_state_incomplete" not in str(body)


def test_capabilities_do_not_share_a_prescription_shaped_field() -> None:
    """Only the code and the status are shared. Forcing `prescription_available` onto a
    readiness endpoint would be a lie dressed as consistency."""
    readiness = CanonicalStateInvalid("readiness", "canonical_state_malformed").to_response_body()
    benchmark = CanonicalStateInvalid("benchmark", "canonical_state_malformed").to_response_body()

    assert readiness["readiness_available"] is False
    assert "prescription_available" not in readiness
    assert benchmark["benchmark_update_applied"] is False
    assert readiness["error"] == benchmark["error"] == "canonical_state_invalid"


# ── :275, the load-or-initialize branch ───────────────────────────────────────


async def test_damaged_existing_state_refuses_and_does_not_initialize(
    async_db, http_client, monkeypatch
) -> None:
    """The distinction the whole branch turns on: a damaged athlete is not a new athlete.

    If initialization keyed off the decode failure instead of the absent row, this athlete's
    real history would be silently replaced by a fresh beginner baseline — and the
    prescription would look perfectly normal.
    """
    hdr = await _register(http_client, "damaged@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, EMPTY_VECTORS)
    before = await _state_count(async_db, uid)

    def _never(*_a, **_k):
        raise AssertionError("initialize_athlete_state called for an EXISTING damaged athlete")

    monkeypatch.setattr("app.services.state_service.initialize_athlete_state", _never)

    resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 409, resp.text
    assert resp.json() == {
        "error": "canonical_state_invalid",
        "prescription_available": False,
        "resolution_available_in_product": False,
    }
    assert await _state_count(async_db, uid) == before  # no replacement row


async def test_legacy_null_payload_row_is_refused_not_reseeded(async_db, http_client) -> None:
    """A row with NULL engine_state is a ROW — an existing athlete, not a new one.

    `except MissingEngineState: default()` would overwrite their history with a default
    constructor and call it onboarding. Initialization keys off the absent row only.
    """
    hdr = await _register(http_client, "legacyrow@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, None)
    before = await _state_count(async_db, uid)

    resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "canonical_state_invalid"
    assert await _state_count(async_db, uid) == before


async def test_future_version_state_refuses_without_touching_the_row(
    async_db, http_client
) -> None:
    hdr = await _register(http_client, "futurerx@t.io")
    uid = await _user_id(http_client, hdr)
    payload = {"version": 99, "x": {"max_strength": 200.0}, "f": {}, "t": {}, "v3_only": 7}
    row = await _damaged_state(async_db, uid, payload)

    resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 409, resp.text
    await async_db.refresh(row)
    assert row.engine_state == payload  # never restamped, never downgraded


async def test_new_athlete_with_no_state_is_still_initialized(async_db, http_client) -> None:
    """Strictness must not break onboarding: no row at all is a legitimate new athlete."""
    hdr = await _register(http_client, "brandnew@t.io")
    uid = await _user_id(http_client, hdr)
    assert await _state_count(async_db, uid) == 0

    resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 200, resp.text
    assert await _state_count(async_db, uid) == 1  # baseline seeded


async def test_healthy_athlete_is_unaffected(async_db, http_client) -> None:
    hdr = await _register(http_client, "healthyrx@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, default_engine_state_dict())

    resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 200, resp.text


# ── no sizing may happen behind the refusal ───────────────────────────────────


async def test_refusal_happens_before_any_load_sizing(async_db, http_client, monkeypatch) -> None:
    """`_enrich_exercises_with_load` is where %e1RM becomes kg. It must never be reached
    with state the codec refused — that is the empty-vector-sizes-load defect itself."""
    hdr = await _register(http_client, "nosizing@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, EMPTY_VECTORS)

    async def _never(*_a, **_k):
        raise AssertionError("_enrich_exercises_with_load reached with refused state")

    monkeypatch.setattr(prescription_service, "_enrich_exercises_with_load", _never)

    resp = await http_client.get("/v1/next-session", headers=hdr)
    assert resp.status_code == 409, resp.text


async def test_planning_route_refuses_without_persistence_side_effects(
    async_db, http_client
) -> None:
    """/planning/today persists the prescription INTO a session slot, so a refusal crossing
    that boundary could leave a partial write behind.

    The pending session scheduled today is load-bearing: without it the route short-circuits
    on "no session" and returns 200 before prescription is ever reached, and the test proves
    nothing while looking green.
    """
    hdr = await _register(http_client, "planrefuse@t.io")
    uid = await _user_id(http_client, hdr)

    block = MesocycleBlock(
        user_id=uid,
        goal=BlockGoal.STRENGTH,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=date.today() - timedelta(days=7),
    )
    async_db.add(block)
    await async_db.flush()
    session = PlannedSession(
        block_id=block.id,
        user_id=uid,
        scheduled_date=date.today(),
        week_number=2,
        day_of_week=1,
        category="Heavy Lower",
        modality="Strength",
        status=SessionStatus.PENDING,
    )
    async_db.add(session)
    await async_db.commit()

    await _damaged_state(async_db, uid, EMPTY_VECTORS)
    before = await _state_count(async_db, uid)

    resp = await http_client.get("/v1/planning/today", headers=hdr)

    assert resp.status_code == 409, resp.text  # reached prescription, and refused
    assert resp.json()["error"] == "canonical_state_invalid"

    # No prescription persisted into the slot, and no state row written.
    await async_db.refresh(session)
    assert session.prescribed_content is None
    assert session.status == SessionStatus.PENDING
    assert await _state_count(async_db, uid) == before


# ── the defect alarm must not look like a refusal ─────────────────────────────


async def test_untranslated_codec_error_is_a_500_defect_not_a_409(
    async_db, http_client, monkeypatch, caplog
) -> None:
    """A raw codec error reaching HTTP is a missed translation — a defect, not a refusal.

    This is why the global handler maps `CanonicalStateInvalid` and NOT
    `EngineStateDecodeError`. If the raw exception were mapped to 409, a new route that
    forgot to translate would silently look correct, and 2B2/2B3/2B4 could ship a hole
    each. It must stay loud and opaque.
    """
    hdr = await _register(http_client, "untranslated@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, default_engine_state_dict())  # healthy: load succeeds

    async def _raw(*_a, **_k):
        raise MalformedCurrentEngineState("vector_validation_failed", "simulated")

    # A helper deeper in the graph reaches the codec and nobody translated it — the
    # realistic shape of the defect. It runs well past the wrapped state load, so the
    # exception escapes raw, exactly as a new untranslated surface would.
    monkeypatch.setattr(prescription_service, "_enrich_exercises_with_load", _raw)

    with caplog.at_level(logging.ERROR):
        resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 500, resp.text
    assert resp.json() == {"detail": "Internal server error. Please try again later."}
    assert "canonical_state_invalid" not in resp.text  # not dressed up as a refusal
    assert "untranslated_engine_state_decode_error" in caplog.text


async def test_supported_flow_never_logs_an_untranslated_error(
    async_db, http_client, caplog
) -> None:
    """The inverse of the above: on the real prescription path the translation is present,
    so the defect alarm must stay silent while the refusal still happens."""
    hdr = await _register(http_client, "translated@t.io")
    uid = await _user_id(http_client, hdr)
    await _damaged_state(async_db, uid, EMPTY_VECTORS)

    with caplog.at_level(logging.ERROR):
        resp = await http_client.get("/v1/next-session", headers=hdr)

    assert resp.status_code == 409
    assert resp.json()["error"] == "canonical_state_invalid"
    assert "untranslated_engine_state_decode_error" not in caplog.text
