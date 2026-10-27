"""INT-15 2E — one damaged athlete row, every surface, in one connected flow.

The load-bearing proof that permissive state cannot cross into decision authority. One
athlete, one head whose canonical payload is gone (a legacy row) beside a healthy legacy
mirror, so any permissive path would happily succeed. Then, against that same row:

  display     /v1/state-history, /v1/dashboard/readiness  -> 200, legacy_recovery, degraded
  onboarding  /v1/onboarding/state                        -> 200, state_available=false
  decisions   next-session, readiness, assessment, projection, benchmark, workout
                                                          -> 409 canonical_state_invalid
  shadows     /v1/wellness                                -> 200; recovery records a skip
  storage     the head's engine_state                     -> byte-identical; no row added

The legacy reconstruction helpers may run ONLY for the two display surfaces.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.engine import state_loading
from app.engine.engine_state_codec import payload_hash
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.observation_mapping import ObservationMapping
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.workout_log import WorkoutLog
from app.scripts.seed_benchmarks import BENCHMARKS, MAPPINGS

pytestmark = pytest.mark.asyncio

MEASURED = "run_vo2_field_test_300m_1p5mi"

DECISIONS = [
    ("GET", "/v1/next-session", None, "prescription_available"),
    ("GET", "/v1/readiness", None, "readiness_available"),
    ("GET", "/v1/benchmarks/assessment-surface", None, "assessment_available"),
    ("POST", "/v1/simulate/projection",
     {"goal": "Powerlifting", "weeks": 4, "weekly_volume": 70, "intensity": "hard",
      "recovery": "standard"}, "projection_available"),
    ("POST", "/v1/benchmarks/observations",
     {"benchmark_code": MEASURED, "raw_value": 50.0, "source": "manual"},
     "benchmark_update_applied"),
    ("POST", "/v1/log-workout",
     {"timestamp": datetime.now(UTC).isoformat(), "modality": "Strength",
      "duration_minutes": 60.0, "session_rpe": 7.5, "total_volume_load": 4000.0,
      "estimated_sets": 12, "sleep_quality": 7.0, "life_stress_inverse": 7.0},
     "workout_logged"),
]


async def _seed_catalog(db: AsyncSession) -> None:
    by_code = {row["code"]: row for row in BENCHMARKS}
    definition = BenchmarkDefinition(**by_code[MEASURED])
    db.add(definition)
    await db.flush()
    for m in MAPPINGS:
        if m["benchmark_code"] == MEASURED:
            db.add(ObservationMapping(
                benchmark_definition_id=definition.id,
                target_vector=m["target_vector"], target_key=m["target_key"],
                mapping_type=m["mapping_type"], coefficient=m["coefficient"],
                intercept=m.get("intercept", 0.0), config=m.get("config"),
            ))
    await db.commit()


async def _committed(user_id: int) -> dict[str, Any]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            rows = (await db.execute(
                select(AthleteState).where(AthleteState.user_id == user_id)
                .order_by(AthleteState.id)
            )).scalars().all()

            async def count(model: Any) -> int:
                q = select(func.count()).select_from(model).where(model.user_id == user_id)
                return int((await db.execute(q)).scalar_one())

            return {
                "states": [(r.id, payload_hash(r.engine_state)) for r in rows],
                "observations": await count(BenchmarkObservation),
                "workouts": await count(WorkoutLog),
            }
    finally:
        await engine.dispose()


async def test_one_damaged_row_never_crosses_into_decision_authority(
    http_client: AsyncClient, async_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_catalog(async_db)
    await http_client.post("/auth/register", json={"email": "e2e@t.io", "password": "pw123456"})
    tok = await http_client.post(
        "/auth/token", data={"username": "e2e@t.io", "password": "pw123456"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    uid = (await http_client.get("/auth/me", headers=hdr)).json()["id"]
    async_db.add(AthleteState(
        user_id=uid, timestamp=datetime(2026, 7, 1, 12, 0), engine_state=None,
        c_met_aerobic=300.0, c_nm_force=1400.0, c_struct=100.0, b_met_anaerobic=15000.0,
        f_met_systemic=5.0, f_nm_peripheral=5.0, f_nm_central=5.0, f_struct_damage=5.0,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={},
    ))
    await async_db.commit()
    before = await _committed(uid)

    # Count every legacy reconstruction, by surface.
    reconstructions: list[str] = []
    real = state_loading.capacity_from_legacy
    current = {"surface": ""}

    def _spy(*a: Any, **k: Any) -> Any:
        reconstructions.append(current["surface"])
        return real(*a, **k)

    monkeypatch.setattr(state_loading, "capacity_from_legacy", _spy)

    # Display: shown, and marked.
    current["surface"] = "history"
    history = await http_client.get("/v1/state-history", headers=hdr)
    assert history.status_code == 200
    assert [(r["state_source"], r["degraded"]) for r in history.json()] == [
        ("legacy_recovery", True)
    ]
    current["surface"] = "dashboard"
    dash = (await http_client.get("/v1/dashboard/readiness", headers=hdr)).json()
    assert (dash["state_source"], dash["degraded"]) == ("legacy_recovery", True)

    # Onboarding: unavailable, never recovered, never re-seeded.
    current["surface"] = "onboarding"
    onb = await http_client.get("/v1/onboarding/state", headers=hdr)
    assert onb.status_code == 200
    assert (onb.json()["twin"]["seeded"], onb.json()["twin"]["state_available"]) == (True, False)

    # Every decision refuses, each with its own availability field.
    for method, route, body, field in DECISIONS:
        current["surface"] = route
        res = (
            await http_client.get(route, headers=hdr) if method == "GET"
            else await http_client.post(route, json=body, headers=hdr)
        )
        assert res.status_code == 409, (route, res.status_code, res.text)
        assert res.json()["error"] == "canonical_state_invalid", route
        assert res.json()[field] is False, route
        # Production closes a refused request's session without committing. `http_client`
        # shares the TEST session across requests (conftest.py), so end it the same way;
        # otherwise the next request's commit would publish this one's flushed rows.
        await async_db.rollback()

    # Shadows: the check-in is saved; the recovery shadow records a skip, not a computation.
    current["surface"] = "wellness"
    well = await http_client.post("/v1/wellness", headers=hdr, json={
        "date": date.today().isoformat(), "source": "manual", "soreness": 6.0,
        "sleep_hours": 7.0, "hrv_ms": 60.0, "resting_hr": 52.0, "mood": 6.0,
    })
    assert well.status_code == 200, well.text
    shadows = (await async_db.execute(
        select(RecoveryShadowLog).where(RecoveryShadowLog.user_id == uid)
    )).scalars().all()
    assert [s.decision_impact for s in shadows] == ["skipped_missing"]

    # Reconstruction happened ONLY where display recovery is approved.
    assert set(reconstructions) <= {"history", "dashboard"}
    assert {"history", "dashboard"} <= set(reconstructions)

    # Storage: nothing written, nothing repaired, nothing re-seeded.
    assert await _committed(uid) == before
