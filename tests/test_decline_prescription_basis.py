"""T7 — candidate-aware prescription basis (INT-02, ADR-0066, fork C staged).

Pure tests run anywhere; DB tests require Postgres.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service
from app.services import strength_decline_service as sds

RULES = {"floor": 40.0, "cap": 250.0}


# --------------------------------------------------------------------------- #
# Pure
# --------------------------------------------------------------------------- #

def test_axis_to_raw_projection():
    assert sds.axis_to_raw(50.0, RULES) == pytest.approx(40.0 + 0.5 * 210.0)  # 145
    assert sds.axis_to_raw(50.0, None) is None
    assert sds.axis_to_raw(50.0, {"floor": 40.0}) is None  # missing cap


def test_select_basis_mode_gating():
    assert sds.select_basis(mode=sds.BASIS_MODE_OFF, legacy=138.0, candidate_aware=144.0) == 138.0
    assert sds.select_basis(mode=sds.BASIS_MODE_SHADOW, legacy=138.0, candidate_aware=144.0) == 138.0
    assert sds.select_basis(mode=sds.BASIS_MODE_ON, legacy=138.0, candidate_aware=144.0) == 144.0


# --------------------------------------------------------------------------- #
# DB resolver across modes
# --------------------------------------------------------------------------- #

async def _active_candidate_setup(db, email: str) -> int:
    from app.services.state_service import initialize_athlete_state
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code="pl_e1rm_squat",
    ))
    d = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules=RULES,
    )
    db.add(d)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=d.id, target_vector="capacity",
        target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()
    await initialize_athlete_state(db, u.id)
    base = datetime.now(UTC) + timedelta(minutes=1)
    for raw, off in ((150.0, 0), (138.0, 10)):
        await benchmark_service.create_observation(
            db, u.id,
            BenchmarkObservationCreate(
                benchmark_code="pl_e1rm_squat", raw_value=raw, source="benchmark_test",
                observed_at=(base + timedelta(days=off)).replace(tzinfo=None),
            ),
        )
    return u.id


@pytest.mark.asyncio
async def test_off_selects_legacy_latest_raw(async_db):
    user_id = await _active_candidate_setup(async_db, "b1@test.com")
    d = await sds.resolve_prescription_basis(
        async_db, user_id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_OFF,
    )
    assert d.selected_basis == pytest.approx(138.0)  # legacy latest raw


@pytest.mark.asyncio
async def test_shadow_records_both_but_selects_legacy(async_db):
    user_id = await _active_candidate_setup(async_db, "b2@test.com")
    d = await sds.resolve_prescription_basis(
        async_db, user_id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_SHADOW,
    )
    assert d.selected_basis == pytest.approx(138.0)          # still legacy
    assert d.ceiling is not None and d.candidate_id is not None
    assert d.candidate_aware_basis < d.legacy_basis + 20     # both computed
    assert d.candidate_aware_basis == pytest.approx(min(d.normal_basis, d.ceiling))


@pytest.mark.asyncio
async def test_on_uses_candidate_aware_and_drops_latest_raw_authority(async_db):
    user_id = await _active_candidate_setup(async_db, "b3@test.com")
    d = await sds.resolve_prescription_basis(
        async_db, user_id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_ON,
    )
    # The chronologically-latest raw (138) is NOT the basis anymore.
    assert d.selected_basis != pytest.approx(138.0)
    assert d.selected_basis == pytest.approx(d.candidate_aware_basis)
    # Conservative middle: capped by the candidate ceiling, below the 150 watermark.
    assert d.selected_basis == pytest.approx(d.ceiling)
    assert d.selected_basis < 150.0
