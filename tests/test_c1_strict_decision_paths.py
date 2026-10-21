"""INT-15 C1 — decision paths refuse untrusted state, and the refusal writes nothing.

The shared 409/500 contract for every capability lives in `test_strict_authority_boundary.py`.
This file pins what C1 adds on the paths that MUTATE canonical state — a benchmark
observation (2B3) and a logged workout — plus the projection:

1. A head that exists but cannot be decoded refuses the whole command, and nothing is left
   behind: no observation, no workout log, no state row, the head's payload unchanged.
2. A damaged row is never "no state": it is not re-seeded as a fresh baseline.
3. The legacy reconstruction is never reached from these paths.
4. Absence still initializes: an athlete with no row at all is seeded exactly as before.
5. The B2 display-only run stays recordable over damaged state, and acquires no authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.engine.engine_state_codec import payload_hash
from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.logic import vo2_estimate as vo2
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.models.weak_point import WeakPoint
from app.models.workout_log import WorkoutLog
from app.scripts.seed_benchmarks import BENCHMARKS, MAPPINGS

pytestmark = pytest.mark.asyncio

MEASURED = "run_vo2_field_test_300m_1p5mi"
TIME_TRIAL = vo2.PROTOCOL_CODE

# Every way a head can exist without canonical state. Healthy legacy scalars sit beside each,
# so a permissive loader would succeed on all of them.
DAMAGED = [
    pytest.param(None, id="null-legacy-row"),
    pytest.param({"version": 2, "x": {}, "f": {}, "t": {}}, id="empty-vectors"),
    pytest.param({"version": 2, "x": {"aerobic": 1.0}}, id="missing-vectors"),
    pytest.param({"version": 2, "x": "not-an-object", "f": {}, "t": {}}, id="malformed"),
    pytest.param({"version": 99, "x": {}, "f": {}, "t": {}}, id="future-version"),
]

WORKOUT = {
    "modality": "Strength",
    "duration_minutes": 60.0,
    "session_rpe": 7.5,
    "total_volume_load": 4000.0,
    "estimated_sets": 12,
    "sleep_quality": 7.0,
    "life_stress_inverse": 7.0,
}


async def _seed_catalog(db: AsyncSession) -> None:
    by_code = {row["code"]: row for row in BENCHMARKS}
    defs = {code: BenchmarkDefinition(**by_code[code]) for code in (MEASURED, TIME_TRIAL)}
    db.add_all(defs.values())
    await db.flush()
    for m in MAPPINGS:
        if m["benchmark_code"] in defs:
            db.add(ObservationMapping(
                benchmark_definition_id=defs[m["benchmark_code"]].id,
                target_vector=m["target_vector"], target_key=m["target_key"],
                mapping_type=m["mapping_type"], coefficient=m["coefficient"],
                intercept=m.get("intercept", 0.0), config=m.get("config"),
            ))
    await db.commit()


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


async def _damaged_head(db: AsyncSession, user_id: int, payload: Any) -> AthleteState:
    row = AthleteState(
        user_id=user_id,
        timestamp=datetime(2026, 7, 1, 12, 0),
        c_met_aerobic=300.0, c_nm_force=1400.0, c_struct=100.0, b_met_anaerobic=15000.0,
        f_met_systemic=5.0, f_nm_peripheral=5.0, f_nm_central=5.0, f_struct_damage=5.0,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={},
        engine_state=payload,
    )
    db.add(row)
    await db.commit()
    return row


async def _count(db: AsyncSession, model: Any, user_id: int) -> int:
    res = await db.execute(select(func.count()).select_from(model).where(model.user_id == user_id))
    return int(res.scalar_one())


async def _snapshot(user_id: int) -> dict[str, Any]:
    """Everything a refused command must leave exactly as it was — read as COMMITTED data.

    Through its own connection, never the test session: `http_client` injects the test's
    session as the request's (conftest.py), so reading through it would see the request's
    flushed-but-uncommitted rows. Production closes the request session without a commit,
    which rolls them back; a separate connection sees exactly what survives that.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            rows = (await db.execute(
                select(AthleteState).where(AthleteState.user_id == user_id)
                .order_by(AthleteState.id)
            )).scalars().all()
            return {
                "states": [(r.id, r.timestamp, payload_hash(r.engine_state)) for r in rows],
                "observations": await _count(db, BenchmarkObservation, user_id),
                "workouts": await _count(db, WorkoutLog, user_id),
                "weak_points": await _count(db, WeakPoint, user_id),
            }
    finally:
        await engine.dispose()


@pytest.fixture
def no_legacy_reconstruction(monkeypatch: pytest.MonkeyPatch, http_client: AsyncClient) -> None:
    # Depends on http_client so the app (and every module binding these names at import) is
    # imported BEFORE the patch; patched first, a by-name import would keep the raiser.
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("legacy reconstruction reached from a decision path")

    for target in ("app.engine.state_loading.capacity_from_legacy",
                   "app.engine.state_bridge.capacity_from_legacy"):
        monkeypatch.setattr(target, _boom)


def _measured() -> dict[str, Any]:
    return {"benchmark_code": MEASURED, "raw_value": 50.0, "source": "manual"}


def _workout() -> dict[str, Any]:
    return {**WORKOUT, "timestamp": datetime.now(UTC).isoformat()}


# --- 1-3. A damaged head refuses atomically, never initializes, never reconstructs -------


@pytest.mark.usefixtures("no_legacy_reconstruction")
@pytest.mark.parametrize("payload", DAMAGED)
@pytest.mark.parametrize(
    ("route", "body", "field"),
    [
        pytest.param("/v1/benchmarks/observations", _measured, "benchmark_update_applied",
                     id="benchmark"),
        pytest.param("/v1/log-workout", _workout, "workout_logged", id="workout"),
        pytest.param("/v1/simulate/projection",
                     lambda: {"goal": "Powerlifting", "weeks": 4, "weekly_volume": 70,
                              "intensity": "hard", "recovery": "standard"},
                     "projection_available", id="projection"),
    ],
)
async def test_a_damaged_head_refuses_and_leaves_nothing_behind(
    http_client: AsyncClient, async_db: AsyncSession, payload: Any, route: str, body: Any,
    field: str,
) -> None:
    await _seed_catalog(async_db)
    hdr, uid = await _athlete(http_client, f"c1-{field}-{payload_hash(payload)[:8]}@t.io")
    await _damaged_head(async_db, uid, payload)
    before = await _snapshot(uid)

    resp = await http_client.post(route, json=body(), headers=hdr)
    await async_db.rollback()  # what production's request-session close does after a raise

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "canonical_state_invalid"
    assert resp.json()[field] is False
    # Atomic: no observation, no workout log, no weak point, and the chain is byte-identical —
    # in particular no baseline was staged over the damaged row (it is a row, not absence).
    assert await _snapshot(uid) == before


# --- 4. Absence still initializes ---------------------------------------------------------


async def test_a_first_workout_still_seeds_the_baseline(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    hdr, uid = await _athlete(http_client, "c1-fresh-workout@t.io")
    assert await _count(async_db, AthleteState, uid) == 0

    resp = await http_client.post("/v1/log-workout", json=_workout(), headers=hdr)

    assert resp.status_code == 200, resp.text
    assert await _count(async_db, WorkoutLog, uid) == 1
    assert await _count(async_db, AthleteState, uid) == 2  # S0 + the workout's row


async def test_a_first_measured_benchmark_still_seeds_the_baseline(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    await _seed_catalog(async_db)
    hdr, uid = await _athlete(http_client, "c1-fresh-benchmark@t.io")

    resp = await http_client.post("/v1/benchmarks/observations", json=_measured(), headers=hdr)

    assert resp.status_code == 200, resp.text
    assert await _count(async_db, BenchmarkObservation, uid) == 1
    assert await _count(async_db, AthleteState, uid) == 2  # staged S0 + the benchmark's row


async def test_a_projection_without_state_previews_and_writes_nothing(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    hdr, uid = await _athlete(http_client, "c1-fresh-projection@t.io")

    resp = await http_client.post(
        "/v1/simulate/projection",
        json={"goal": "Powerlifting", "weeks": 4, "weekly_volume": 70,
              "intensity": "hard", "recovery": "standard"},
        headers=hdr,
    )

    assert resp.status_code == 200, resp.text
    assert await _count(async_db, AthleteState, uid) == 0


# --- 5. The B2 display-only run over damaged state ----------------------------------------


@pytest.mark.usefixtures("no_legacy_reconstruction")
@pytest.mark.parametrize("payload", DAMAGED)
async def test_the_display_only_run_is_recordable_over_damaged_state(
    http_client: AsyncClient, async_db: AsyncSession, payload: Any,
) -> None:
    """The run never reads state, so damaged state cannot refuse it — and it gains nothing."""
    await _seed_catalog(async_db)
    hdr, uid = await _athlete(http_client, f"c1-run-{payload_hash(payload)[:8]}@t.io")
    await _damaged_head(async_db, uid, payload)
    before = await _snapshot(uid)

    resp = await http_client.post("/v1/benchmarks/observations", headers=hdr, json={
        "benchmark_code": TIME_TRIAL, "raw_value": 720.0, "source": "manual",
        # The writer asks for authority; the protocol refuses it regardless of state.
        "requested_capacity_effect": oa.CE_BIDIRECTIONAL_UPDATE, "affects_prescription": True,
    })

    assert resp.status_code == 200, resp.text
    after = await _snapshot(uid)
    assert after["states"] == before["states"]  # no state row, head untouched
    assert after["observations"] == before["observations"] + 1
    assert after["weak_points"] == before["weak_points"]
    [row] = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == uid)
    )).scalars().all()
    assert (row.evidence_type, row.capacity_effect, row.affects_prescription) == (
        se.EV_DISPLAY_ONLY_PERFORMANCE, oa.CE_NONE, False
    )


async def test_onboarding_records_the_run_for_an_athlete_with_damaged_state(
    http_client: AsyncClient, async_db: AsyncSession,
) -> None:
    """Onboarding over an existing damaged row: no baseline is staged over it (has_state), the
    run is saved display-only, and the chain is untouched."""
    await _seed_catalog(async_db)
    hdr, uid = await _athlete(http_client, "c1-onboard-damaged@t.io")
    await _damaged_head(async_db, uid, {"version": 2, "x": {}, "f": {}, "t": {}})
    before = await _snapshot(uid)

    resp = await http_client.post("/v1/onboard", headers=hdr, json={
        "goal": "Running", "date_of_birth": "2000-05-01", "equipment": ["bodyweight"],
        "available_days_per_week": 4, "session_duration_minutes": 45,
        "run_time_trial": {"elapsed_seconds": 720, "performed_on": "2026-10-01",
                           "body_mass": 74, "body_mass_unit": "kg", "sex_for_estimate": "male"},
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["run_time_trial"]["status"] == "estimated"
    assert (await _snapshot(uid))["states"] == before["states"]
