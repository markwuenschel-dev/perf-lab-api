"""Why a prescribed exercise has no suggested weight — the N1 contract (S2 decision 4).

Every prescribed exercise says whether it carries a suggested weight, and if not, why:

* a lift with an e1RM benchmark is ``recommended`` or ``no_qualifying_evidence`` with the
  selector's athlete-facing category (``explain_missing_basis``);
* an externally loaded exercise with no benchmark is ``not_supported`` — a different thing
  to tell an athlete than "your evidence does not qualify";
* an unloaded or uncatalogued exercise has nothing to explain.

The explanation carries the instant it was evaluated and, when a deterministic row exists,
when that evidence was performed. It is persisted with the served prescription. The last
test drives the real ``/v1/planning/today`` route and reads the stored JSONB back.

Evidence rows are inserted directly: this file is about what the prescription says about
evidence, not about how evidence is written.
"""
from datetime import UTC, date, datetime, timedelta
from typing import Any, get_args

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import get_current_user
from app.core.db import get_db
from app.logic import observation_authority as oa
from app.logic import prescription_evidence as pe
from app.logic import strength_evidence as se
from app.main import app
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.schemas.prescription import (
    ExercisePrescription,
    LoadExplanation,
    LoadExplanationReason,
    WorkoutPrescription,
)
from app.services import prescription_service
from app.services.prescription_service import _enrich_exercises_with_load

SQUAT = "pl_e1rm_squat"
#: Every enrichment below is evaluated at this instant (naive UTC, as stored).
AS_OF = datetime(2026, 9, 14, 12, 0, 0)
_TWO_DAYS_AGO = AS_OF - timedelta(days=2)
_BLOCK = {"week_number": 1, "duration_weeks": 4}

#: (name, movement pattern, load_type, e1RM benchmark code)
_CATALOG = (
    ("Back Squat", "squat", "barbell", SQUAT),
    ("Dumbbell Row", "pull_horizontal", "dumbbell", None),
    ("Push-up", "push_horizontal", "bodyweight", None),
)


def _aware(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.replace(tzinfo=UTC)


async def _athlete(db: Any, email: str) -> tuple[User, int]:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    for name, pattern, load_type, code in _CATALOG:
        db.add(Exercise(
            name=name, modality="Strength", movement_pattern=pattern, load_type=load_type,
            is_benchmark=code is not None, e1rm_benchmark_code=code,
        ))
    definition = BenchmarkDefinition(
        code=SQUAT, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.commit()
    await db.refresh(user)
    await db.refresh(definition)
    return user, definition.id


def _tested_max(performed_at: datetime | None) -> dict[str, Any]:
    return {
        "raw_value": 140.0, "performed_at": performed_at,
        "evidence_type": se.EV_DIRECT_MEASUREMENT, "value_semantics": se.VS_MEASURED,
    }


_ESTIMATE = {
    "raw_value": 150.0, "performed_at": _TWO_DAYS_AGO, "affects_prescription": False,
    "evidence_type": se.EV_REPORTED_ESTIMATE, "value_semantics": se.VS_ESTIMATED,
}
#: A set of eight: saved, characterized, and too far from failure to size a load.
_SET_OF_EIGHT = {
    "raw_value": 100.0, "performed_at": _TWO_DAYS_AGO, "reps": 8, "load_kg": 80.0, "rpe": 9.0,
    "effort_fidelity": se.FIDELITY_SET_LEVEL, "formula": "epley",
    "evidence_type": se.EV_ESTIMATED_FROM_TRAINING_SET, "value_semantics": se.VS_ESTIMATED,
}
_UNKNOWN_PROVENANCE = {**_tested_max(_TWO_DAYS_AGO), "source_type": "legacy_unknown"}


async def _record(db: Any, user_id: int, definition_id: int, fields: dict[str, Any]) -> None:
    row = {
        "user_id": user_id, "benchmark_definition_id": definition_id, "source": "manual",
        "source_type": oa.ST_ATHLETE_ENTRY, "validity_status": "valid",
        "affects_prescription": True, **fields,
    }
    db.add(BenchmarkObservation(**row))
    await db.commit()


def _rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="strength", focus="lower", rationale="x", duration_min=60,
        exercises=[
            ExercisePrescription(name="Back Squat", sets=3, reps="5", load_note="Autoregulate by RPE"),
            ExercisePrescription(name="Dumbbell Row", sets=3, reps="10", load_note="Autoregulate by RPE"),
            ExercisePrescription(name="Push-up", sets=3, reps="12"),
            ExercisePrescription(name="Mystery Move", sets=2, reps="8"),
        ],
    )


def test_the_published_reasons_are_exactly_the_selector_categories() -> None:
    assert set(get_args(LoadExplanationReason)) == {
        pe.EXPLAIN_STALE, pe.EXPLAIN_MISSING_DATE, pe.EXPLAIN_ESTIMATE_NOT_USED,
        pe.EXPLAIN_SET_NOT_QUALIFYING, pe.EXPLAIN_NO_EVIDENCE, pe.EXPLAIN_NOT_QUALIFYING,
    }


@pytest.mark.parametrize(("rows", "reason", "performed_at"), [
    pytest.param([], pe.EXPLAIN_NO_EVIDENCE, None, id="no_evidence"),
    pytest.param(
        [_tested_max(AS_OF - timedelta(days=40))], pe.EXPLAIN_STALE, AS_OF - timedelta(days=40),
        id="stale",
    ),
    pytest.param([_tested_max(None)], pe.EXPLAIN_MISSING_DATE, None, id="missing_performance_date"),
    pytest.param([_ESTIMATE], pe.EXPLAIN_ESTIMATE_NOT_USED, _TWO_DAYS_AGO, id="estimate_not_used"),
    pytest.param([_SET_OF_EIGHT], pe.EXPLAIN_SET_NOT_QUALIFYING, _TWO_DAYS_AGO, id="set_not_qualifying"),
    pytest.param([_UNKNOWN_PROVENANCE], pe.EXPLAIN_NOT_QUALIFYING, _TWO_DAYS_AGO, id="not_qualifying"),
])
async def test_a_lift_without_qualifying_evidence_says_why_and_keeps_its_effort_guidance(
    async_db, rows, reason, performed_at
):
    user, definition_id = await _athlete(async_db, f"n1-{reason}@test.com")
    for row in rows:
        await _record(async_db, user.id, definition_id, row)

    rx = _rx()
    await _enrich_exercises_with_load(async_db, user.id, rx, _BLOCK, as_of=AS_OF)

    squat = rx.exercises[0]
    assert squat.prescribed_load_kg is None
    assert squat.load_note == "Autoregulate by RPE"
    assert squat.load_explanation == LoadExplanation(
        status="no_qualifying_evidence", reason=reason, benchmark_code=SQUAT,
        evaluated_at=AS_OF.replace(tzinfo=UTC), evidence_performed_at=_aware(performed_at),
    )


async def test_a_qualifying_lift_is_recommended_and_other_exercises_are_classified(async_db):
    user, definition_id = await _athlete(async_db, "n1-recommended@test.com")
    await _record(async_db, user.id, definition_id, _tested_max(_TWO_DAYS_AGO))

    rx = _rx()
    await _enrich_exercises_with_load(async_db, user.id, rx, _BLOCK, as_of=AS_OF)

    squat, row, push_up, unknown = rx.exercises
    evaluated_at = AS_OF.replace(tzinfo=UTC)
    assert squat.prescribed_load_kg is not None
    assert squat.load_explanation == LoadExplanation(
        status="recommended", benchmark_code=SQUAT, evaluated_at=evaluated_at,
        evidence_performed_at=_aware(_TWO_DAYS_AGO),
    )
    # Loaded, but no benchmark could ever size it: not the same as missing evidence.
    assert row.load_explanation == LoadExplanation(status="not_supported", evaluated_at=evaluated_at)
    assert push_up.load_explanation is None
    assert unknown.load_explanation is None


async def test_a_session_with_no_benchmarked_lift_is_still_explained(async_db):
    """The enrichment used to return before touching anything when no exercise had a
    benchmark; the explanation must not share that early exit."""
    user, _ = await _athlete(async_db, "n1-no-lift@test.com")
    rx = _rx()
    rx.exercises = rx.exercises[1:]

    await _enrich_exercises_with_load(async_db, user.id, rx, _BLOCK, as_of=AS_OF)

    assert [ex.load_explanation and ex.load_explanation.status for ex in rx.exercises] == [
        "not_supported", None, None,
    ]


async def _get_today(db: Any, user: User) -> dict[str, Any]:
    async def _override_db():
        yield db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/planning/today", params={"goal": "Strength"})
            assert resp.status_code == 200, resp.text
            return resp.json()
    finally:
        app.dependency_overrides.clear()


async def test_the_served_prescription_carries_the_explanation_and_persists_it(async_db, monkeypatch):
    """Through the real route: enrichment runs on the served prescription, the response
    carries aware-UTC times, and the stored JSONB holds the same explanations."""
    user, definition_id = await _athlete(async_db, "n1-route@test.com")
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    now = datetime.now(UTC).replace(tzinfo=None)
    await _record(async_db, user.id, definition_id, _tested_max(now - timedelta(days=2)))
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=date.today(), deload_every_n_weeks=4,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)
    session = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=date.today(), week_number=1,
        day_of_week=date.today().isoweekday(), category="Heavy Lower", modality="Strength",
    )
    async_db.add(session)
    await async_db.commit()
    await async_db.refresh(session)
    # The scorer's choice of exercises is not under test; the enrichment and persistence are.
    monkeypatch.setattr(prescription_service, "_score_prescription", lambda _state, _ctx: _rx())

    payload = await _get_today(async_db, user)

    explanations = [ex["load_explanation"] for ex in payload["prescription"]["exercises"]]
    assert [e and e["status"] for e in explanations] == ["recommended", "not_supported", None, None]
    evaluated_at = datetime.fromisoformat(explanations[0]["evaluated_at"])
    assert evaluated_at.utcoffset() == timedelta(0)
    assert abs(evaluated_at - datetime.now(UTC)) < timedelta(minutes=5)

    stored = (await async_db.execute(
        select(PlannedSession.prescribed_content).where(PlannedSession.id == session.id)
    )).scalar_one()
    assert [ex["load_explanation"] for ex in stored["exercises"]] == explanations
