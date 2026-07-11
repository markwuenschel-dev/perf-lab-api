"""ADR-0058 — deferred upward_lower_bound floor-ratchet is recorded as shadow evidence.

Resolved authority and applied transition are recorded separately: a qualifying
workout-extraction observation writes a capacity_floor_shadow_log candidate (proposed
floor, projected uplift, application-policy version, not-applied reason) while canonical
capacity stays untouched — the deployed ADR-0055 invariant remains active.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.logic import observation_authority as oa
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.capacity_floor_shadow import CapacityFloorShadowLog
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.state import UnifiedStateVector
from app.services import benchmark_service, capacity_floor_shadow_service
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


# ── pure: the candidate payload ───────────────────────────────────────────────

def _vec(**caps: float) -> UnifiedStateVector:
    v = UnifiedStateVector(
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        c_met_aerobic=300.0, c_nm_force=400.0, c_struct=50.0, b_met_anaerobic=50.0,
    )
    for key, val in caps.items():
        setattr(v.capacity_x, key, val)
    return v


def test_floor_candidate_payload_uplift_and_reason() -> None:
    prior = _vec(max_strength=40.0, power=50.0)
    floored = _vec(max_strength=55.0, power=50.0)  # max_strength rises, power unchanged
    p = capacity_floor_shadow_service.floor_candidate_payload(prior, floored)
    assert p["would_raise"] is True
    assert p["projected_uplift"]["max_strength"] == pytest.approx(15.0)
    assert "power" not in p["projected_uplift"]  # no delta → not listed
    assert p["projected_uplift_total"] == pytest.approx(15.0)
    assert p["not_applied_reason"] == oa.FLOOR_NOT_APPLIED_DEFERRED


def test_floor_candidate_payload_below_watermark() -> None:
    prior = _vec(max_strength=100.0)
    floored = _vec(max_strength=100.0)  # identical → nothing raised
    p = capacity_floor_shadow_service.floor_candidate_payload(prior, floored)
    assert p["would_raise"] is False
    assert p["projected_uplift"] == {}
    assert p["not_applied_reason"] == oa.FLOOR_NOT_APPLIED_BELOW_WATERMARK


# ── DB: the write path captures the candidate, mutates no state ────────────────

async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db) -> None:
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
    definition = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=definition.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


async def _shadow_rows(db, user_id: int) -> list[CapacityFloorShadowLog]:
    r = await db.execute(
        select(CapacityFloorShadowLog).where(CapacityFloorShadowLog.user_id == user_id)
    )
    return list(r.scalars().all())


async def _state_count(db, user_id: int) -> int:
    r = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(r.scalar_one())


async def test_workout_extraction_records_floor_shadow_no_state_mutation(async_db):
    user = await _user(async_db, "cf1@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    before = await _state_count(async_db, user.id)

    await benchmark_service.create_observation(
        async_db, user.id,
        BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=180.0, source="workout_extraction",
        ),
    )

    # No canonical capacity mutation — the deployed a025 invariant stays active.
    assert await _state_count(async_db, user.id) == before
    # But a shadow candidate is captured, separate from any applied transition.
    rows = await _shadow_rows(async_db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.capacity_effect == oa.CE_UPWARD_LOWER_BOUND
    assert row.decision_impact == "none_shadow_only"
    assert row.application_policy_version == oa.FLOOR_APPLY_POLICY_VERSION
    assert row.authority_policy_version == oa.POLICY_VERSION
    assert row.would_raise is True  # 180kg exceeds the weak baseline → a real floor
    assert row.not_applied_reason == oa.FLOOR_NOT_APPLIED_DEFERRED
    assert row.projected_uplift_total > 0.0
    assert "max_strength" in row.proposed_floor_json
