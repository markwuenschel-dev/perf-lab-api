"""The shared contract every strict authority migration must satisfy (INT-15 W1-A, 2B).

Parameterized over capabilities rather than written per service, so each new migration
(2B3 benchmark, 2B4 onboarding/assessment) adds a row here instead of re-deriving the
boundary — and cannot quietly ship without it.

The invariant, stated once:

* A strict decode failure at an authority-bearing surface becomes an intentional refusal:
  409 + ``canonical_state_invalid`` + that capability's own availability field.
* An **untranslated** ``EngineStateDecodeError`` reaching HTTP stays an opaque 500 and is
  logged as a defect. It must never be dressed up as a 409.

The second half is the one that keeps the first honest. If the raw codec exception were
globally mapped to 409, a surface that forgot to translate would look like it was working.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from app.engine.engine_state_codec import MalformedCurrentEngineState
from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState

pytestmark = pytest.mark.asyncio

EMPTY_VECTORS = {"version": 2, "x": {}, "f": {}, "t": {}}

# capability, route, availability field, and the in-service symbol whose raw escape
# simulates a missed translation on that surface.
MIGRATED_AUTHORITIES = [
    pytest.param(
        "prescription",
        "/v1/next-session",
        "prescription_available",
        "app.services.prescription_service._enrich_exercises_with_load",
        id="prescription",
    ),
    pytest.param(
        "readiness",
        "/v1/readiness",
        "readiness_available",
        "app.services.readiness_service._latest_wellness",
        id="readiness",
    ),
]


async def _register(client, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": "pw123456"})
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "pw123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _user_id(client, hdr) -> int:
    return (await client.get("/auth/me", headers=hdr)).json()["id"]


async def _state(db, user_id: int, payload: object) -> AthleteState:
    """Damaged payload, healthy legacy mirror — a permissive loader would succeed here."""
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


@pytest.mark.parametrize(("capability", "route", "field", "_patch"), MIGRATED_AUTHORITIES)
async def test_damaged_state_is_an_intentional_409_refusal(
    async_db, http_client, capability, route, field, _patch
) -> None:
    hdr = await _register(http_client, f"refuse-{capability}@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)

    resp = await http_client.get(route, headers=hdr)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "canonical_state_invalid"
    assert body[field] is False
    assert body["resolution_available_in_product"] is False
    # The internal reason taxonomy stays internal.
    assert "canonical_state_incomplete" not in resp.text


@pytest.mark.parametrize(("capability", "route", "field", "_patch"), MIGRATED_AUTHORITIES)
async def test_capability_does_not_borrow_another_capabilitys_field(
    async_db, http_client, capability, route, field, _patch
) -> None:
    """Only the code and the status are shared. `prescription_available` on a readiness
    endpoint would be a lie dressed as consistency."""
    hdr = await _register(http_client, f"field-{capability}@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)

    body = (await http_client.get(route, headers=hdr)).json()

    others = {f for _c, _r, f, _p in [p.values for p in MIGRATED_AUTHORITIES]} - {field}
    assert not (others & set(body)), f"{capability} leaked another capability's field"


@pytest.mark.parametrize(("capability", "route", "field", "patch"), MIGRATED_AUTHORITIES)
async def test_untranslated_codec_error_stays_a_500_defect(
    async_db, http_client, monkeypatch, caplog, capability, route, field, patch
) -> None:
    """A raw codec error escaping to HTTP is a MISSED TRANSLATION, not a refusal.

    Deliberately opaque, deliberately loud. This is why the global handler maps
    `CanonicalStateInvalid` and never `EngineStateDecodeError` — otherwise a surface that
    forgot to translate would return a tidy 409 and look correct, and each new 2B slice
    could ship a hole.
    """
    hdr = await _register(http_client, f"raw-{capability}@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, default_engine_state_dict())  # healthy: the load succeeds

    async def _raw(*_a, **_k):
        raise MalformedCurrentEngineState("vector_validation_failed", "simulated")

    # Runs past the translated state load, so the exception escapes raw — the realistic
    # shape of a helper deeper in the graph reaching the codec untranslated.
    monkeypatch.setattr(patch, _raw)

    with caplog.at_level(logging.ERROR):
        resp = await http_client.get(route, headers=hdr)

    assert resp.status_code == 500, resp.text
    assert "canonical_state_invalid" not in resp.text
    assert "untranslated_engine_state_decode_error" in caplog.text


@pytest.mark.parametrize(("capability", "route", "field", "_patch"), MIGRATED_AUTHORITIES)
async def test_translated_path_does_not_trip_the_defect_alarm(
    async_db, http_client, caplog, capability, route, field, _patch
) -> None:
    """The inverse: on the real path the translation is present, so the refusal happens
    while the defect alarm stays silent."""
    hdr = await _register(http_client, f"quiet-{capability}@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)

    with caplog.at_level(logging.ERROR):
        resp = await http_client.get(route, headers=hdr)

    assert resp.status_code == 409
    assert "untranslated_engine_state_decode_error" not in caplog.text


@pytest.mark.parametrize(("capability", "route", "field", "_patch"), MIGRATED_AUTHORITIES)
async def test_healthy_state_is_unaffected(
    async_db, http_client, capability, route, field, _patch
) -> None:
    hdr = await _register(http_client, f"ok-{capability}@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, default_engine_state_dict())

    resp = await http_client.get(route, headers=hdr)

    assert resp.status_code == 200, resp.text
