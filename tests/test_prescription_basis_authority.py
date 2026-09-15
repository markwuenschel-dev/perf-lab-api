"""Which observation sizes the bar — pinned against the ATHLETE-VISIBLE OUTCOME.

``affects_prescription`` is an explicit permission: toggling only that flag True -> False
must leave the next prescription exactly as it was before the observation existed, with
the positive control beside it so a selector that rejects everything cannot pass.

S2 adds two dimensions the original suite could not express. Evidence must be FRESH by
performance time (28 days, provisional), and the two e1RM readers — the prescribed load
and the dose-intensity denominator — agree only for identical evidence AND an identical
``as_of``. A prescription and a later or backdated workout log can straddle an expiry;
these tests pin both the agreement and the straddle, rather than asserting a guarantee
the time inputs do not support.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.services import benchmark_service
from app.services.prescription_service import _enrich_exercises_with_load
from app.services.state_service import prelog_e1rm_denominators

pytestmark = pytest.mark.asyncio

_CODE = "pl_e1rm_squat"
_RULES = {"floor": 40.0, "cap": 250.0}
_BLOCK = {"week_number": 1, "duration_weeks": 4}
#: Every prescription and denominator in this file is evaluated as of this instant.
AS_OF = datetime(2026, 9, 14, 12, 0, 0)


async def _setup(db, email: str) -> User:
    """An athlete with a squat in the catalog and a squat e1RM benchmark defined."""
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code=_CODE,
    ))
    db.add(BenchmarkDefinition(
        code=_CODE, name="Squat e1RM", domain="powerlifting",
        metric_type="load", unit="kg", better_direction="higher",
        observation_weight=1.0, standardization_rules=_RULES,
    ))
    await db.commit()
    await db.refresh(user)
    return user


async def _observe(
    db, user_id: int, raw: float, *, performed_days_ago: float | None, affects: bool = True
) -> None:
    """A characterized tested max, recorded through the service as every real writer does.

    ``performed_days_ago=None`` records it with no performance date.
    """
    performed_at = None if performed_days_ago is None else AS_OF - timedelta(days=performed_days_ago)
    await benchmark_service.create_observation(
        db, user_id,
        BenchmarkObservationCreate(
            benchmark_code=_CODE, raw_value=raw, source="benchmark_test",
            value_semantics="measured", affects_prescription=affects,
        ),
        performed_at=performed_at,
    )


async def _observe_without_stating_the_flag(db, user_id: int, raw: float, *, performed_days_ago: float) -> None:
    """A row whose writer never stated the prescription flag: a genuine SQL NULL.

    Deliberately bypasses ``create_observation`` (which defaults the flag to True).
    """
    def_id = (await db.execute(
        select(BenchmarkDefinition.id).where(BenchmarkDefinition.code == _CODE)
    )).scalar_one()
    db.add(BenchmarkObservation(
        user_id=user_id, benchmark_definition_id=def_id, raw_value=raw,
        source="benchmark_test", source_type="athlete_entry", value_semantics="measured",
        performed_at=AS_OF - timedelta(days=performed_days_ago),
    ))
    await db.commit()


def _squat_rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="strength", focus="squat", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(
            name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE"
        )],
    )


async def _prescribe(db, user_id: int, as_of: datetime = AS_OF) -> ExercisePrescription:
    """The squat as it would be prescribed to this athlete at ``as_of``."""
    rx = _squat_rx()
    await _enrich_exercises_with_load(db, user_id, rx, _BLOCK, as_of=as_of)
    return rx.exercises[0]


# ── explicit permission ──────────────────────────────────────────────────────────

async def test_rejected_observation_leaves_the_prescription_exactly_as_it_was(async_db):
    user = await _setup(async_db, "basis-reject@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=2)
    before = await _prescribe(async_db, user.id)

    # A fresher, heavier observation the athlete explicitly marked unfit to prescribe from.
    await _observe(async_db, user.id, 180.0, performed_days_ago=1, affects=False)
    after = await _prescribe(async_db, user.id)

    assert before.e1rm_basis_kg == 140.0, "precondition: the accepted observation is the basis"
    assert after.e1rm_basis_kg == before.e1rm_basis_kg
    assert after.prescribed_load_kg == before.prescribed_load_kg
    assert after.percent_e1rm == before.percent_e1rm


async def test_the_same_observation_accepted_does_move_the_prescription(async_db):
    """Positive control: a selector that rejects everything must not pass the test above."""
    user = await _setup(async_db, "basis-accept@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=2)
    before = await _prescribe(async_db, user.id)

    await _observe(async_db, user.id, 180.0, performed_days_ago=1)
    after = await _prescribe(async_db, user.id)

    assert after.e1rm_basis_kg == 180.0
    assert after.prescribed_load_kg is not None and before.prescribed_load_kg is not None
    assert after.prescribed_load_kg > before.prescribed_load_kg


async def test_an_observation_that_never_stated_the_flag_is_not_a_basis(async_db):
    """NULL is "nobody said", and absence of a statement is not permission."""
    user = await _setup(async_db, "basis-null@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=2)
    before = await _prescribe(async_db, user.id)

    await _observe_without_stating_the_flag(async_db, user.id, 180.0, performed_days_ago=1)
    after = await _prescribe(async_db, user.id)

    assert after.e1rm_basis_kg == before.e1rm_basis_kg == 140.0
    assert after.prescribed_load_kg == before.prescribed_load_kg


# ── freshness by performance time ────────────────────────────────────────────────

async def test_undated_evidence_never_sizes_a_load(async_db):
    user = await _setup(async_db, "basis-undated@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=None)

    rx = await _prescribe(async_db, user.id)

    assert rx.prescribed_load_kg is None
    assert rx.e1rm_basis_kg is None
    assert rx.load_note == "Autoregulate by RPE"


async def test_submission_time_never_becomes_performance_time(async_db):
    """observed_at still defaults for the record; performed_at stays unknown."""
    user = await _setup(async_db, "basis-submitted@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=None)

    row = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user.id)
    )).scalar_one()
    assert row.observed_at is not None
    assert row.performed_at is None


async def test_stale_evidence_stops_sizing_a_load_without_touching_the_row(async_db):
    """Expiry is evaluated at selection time: the observation itself is not invalidated
    and its flags are not flipped."""
    user = await _setup(async_db, "basis-stale@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=10)

    fresh = await _prescribe(async_db, user.id)
    expired = await _prescribe(async_db, user.id, as_of=AS_OF + timedelta(days=19))

    assert fresh.e1rm_basis_kg == 140.0
    assert expired.prescribed_load_kg is None and expired.e1rm_basis_kg is None
    row = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user.id)
    )).scalar_one()
    assert (row.validity_status, row.affects_prescription) == ("valid", True)


# ── the two readers ──────────────────────────────────────────────────────────────

async def test_readers_agree_on_identical_evidence_and_reference_time(async_db):
    """ADR-0056 across the seam, stated with its precondition: same evidence, same as_of."""
    user = await _setup(async_db, "basis-pairing@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=5)
    await _observe(async_db, user.id, 180.0, performed_days_ago=4, affects=False)
    await _observe(async_db, user.id, 150.0, performed_days_ago=40)  # stale

    prescribed = await _prescribe(async_db, user.id)
    denominators = await prelog_e1rm_denominators(async_db, user.id, {_CODE}, as_of=AS_OF)

    assert denominators[_CODE]["value"] == 140.0
    assert prescribed.e1rm_basis_kg == denominators[_CODE]["value"]


async def test_readers_agree_that_nothing_qualifies(async_db):
    user = await _setup(async_db, "basis-pairing-empty@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=30)

    prescribed = await _prescribe(async_db, user.id)
    denominators = await prelog_e1rm_denominators(async_db, user.id, {_CODE}, as_of=AS_OF)

    assert prescribed.e1rm_basis_kg is None
    assert _CODE not in denominators


async def test_a_prescription_and_a_later_log_can_straddle_expiry(async_db):
    """Agreement is per reference time. Evidence performed 27 days before the prescription
    sizes that prescription; a workout logged two days later is scored without it."""
    user = await _setup(async_db, "basis-straddle@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=27)

    prescribed = await _prescribe(async_db, user.id, as_of=AS_OF)
    logged_later = await prelog_e1rm_denominators(
        async_db, user.id, {_CODE}, as_of=AS_OF + timedelta(days=2)
    )

    assert prescribed.e1rm_basis_kg == 140.0
    assert _CODE not in logged_later


async def test_a_backdated_log_ignores_evidence_performed_after_it(async_db):
    """The dose reference time is the workout's own time: a lift performed after the
    session being logged cannot be that session's pre-log denominator."""
    user = await _setup(async_db, "basis-backdated@test.com")
    await _observe(async_db, user.id, 140.0, performed_days_ago=6)
    await _observe(async_db, user.id, 160.0, performed_days_ago=1)

    backdated_session = AS_OF - timedelta(days=3)
    denominators = await prelog_e1rm_denominators(async_db, user.id, {_CODE}, as_of=backdated_session)

    assert denominators[_CODE]["value"] == 140.0
