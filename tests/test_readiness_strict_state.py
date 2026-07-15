"""INT-15 W1-A slice 2B2 — readiness refuses rather than scoring from untrusted state.

Readiness is not display. It gates prescription (`prescription_service.py:386`), so a
readiness number computed from a legacy reconstruction becomes a prescription input one
call later. The fact that the number also appears on a screen does not make the surface
display-only — it is classified by what it can *do*.

The shared 409/500 boundary is covered once for every migrated capability in
`test_strict_authority_boundary.py`. This file pins what is specific to readiness:
no fallback reconstruction, and no readiness-derived write behind the refusal.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState
from app.models.wellness import WellnessSample
from app.services import readiness_service

pytestmark = pytest.mark.asyncio

EMPTY_VECTORS = {"version": 2, "x": {}, "f": {}, "t": {}}


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


async def _wellness_count(db, user_id: int) -> int:
    from sqlalchemy import func, select

    res = await db.execute(
        select(func.count()).select_from(WellnessSample).where(WellnessSample.user_id == user_id)
    )
    return int(res.scalar_one())


async def _state_count(db, user_id: int) -> int:
    from sqlalchemy import func, select

    res = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(res.scalar_one())


async def test_refusal_never_reconstructs_from_the_legacy_mirror(
    async_db, http_client, monkeypatch
) -> None:
    """The row's legacy scalars are healthy and a permissive loader would happily use them.
    Prove the strict path cannot reach them."""
    hdr = await _register(http_client, "nofallback@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)

    def _boom(*_a, **_k):
        raise AssertionError("legacy reconstruction reached from the readiness path")

    monkeypatch.setattr("app.engine.state_loading.capacity_from_legacy", _boom)
    monkeypatch.setattr("app.engine.state_bridge.capacity_from_legacy", _boom)

    resp = await http_client.get("/v1/readiness", headers=hdr)

    assert resp.status_code == 409, resp.text
    assert resp.json()["readiness_available"] is False


async def test_refusal_writes_nothing(async_db, http_client) -> None:
    """No readiness-derived mutation or sample write behind the refusal."""
    hdr = await _register(http_client, "nowrite@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)
    states_before = await _state_count(async_db, uid)
    wellness_before = await _wellness_count(async_db, uid)

    resp = await http_client.get("/v1/readiness", headers=hdr)

    assert resp.status_code == 409
    assert await _state_count(async_db, uid) == states_before
    assert await _wellness_count(async_db, uid) == wellness_before


async def test_compute_readiness_raises_the_readiness_capability_not_prescription(
    async_db, http_client
) -> None:
    """Called directly as a service, the refusal names readiness — the capability whose
    authority is actually being exercised."""
    from app.core.errors import CanonicalStateInvalid

    hdr = await _register(http_client, "capname@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, EMPTY_VECTORS)

    with pytest.raises(CanonicalStateInvalid) as exc:
        await readiness_service.compute_readiness(async_db, uid)

    assert exc.value.capability == "readiness"
    assert exc.value.normalized_reason == "canonical_state_incomplete"


async def test_future_version_reports_a_deployment_reason_not_damage(
    async_db, http_client
) -> None:
    """Future-version is not damaged data. Internally it stays distinguishable because the
    operational response differs: deploy readers, do not repair the row."""
    from app.core.errors import CanonicalStateInvalid

    hdr = await _register(http_client, "futready@t.io")
    uid = await _user_id(http_client, hdr)
    row = await _state(async_db, uid, {"version": 99, "x": {"a": 1}, "f": {}, "t": {}})

    with pytest.raises(CanonicalStateInvalid) as exc:
        await readiness_service.compute_readiness(async_db, uid)

    assert exc.value.normalized_reason == "canonical_state_version_unsupported"
    await async_db.refresh(row)
    assert row.engine_state["version"] == 99  # untouched, never restamped


async def test_new_athlete_with_no_state_still_scores(async_db, http_client) -> None:
    """`None` (no row) is not a decode failure — readiness must still work for a new athlete."""
    hdr = await _register(http_client, "newready@t.io")

    resp = await http_client.get("/v1/readiness", headers=hdr)

    assert resp.status_code == 200, resp.text


async def test_healthy_state_scores_normally(async_db, http_client) -> None:
    hdr = await _register(http_client, "okready@t.io")
    uid = await _user_id(http_client, hdr)
    await _state(async_db, uid, default_engine_state_dict())

    resp = await http_client.get("/v1/readiness", headers=hdr)

    assert resp.status_code == 200, resp.text
