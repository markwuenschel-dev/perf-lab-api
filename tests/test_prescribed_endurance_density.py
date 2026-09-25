"""Prescribed timed work over logged elapsed time: a shadow-only density proxy (phase 5.4).

What this proxy IS: seconds of WORK in the prescription the log was explicitly linked to,
divided by the seconds the athlete logged. Dimensionless, in (0, 1]. Temporal work density —
not intensity: 20 easy minutes and 20 threshold minutes can share a value.

What it is NOT: performed density. Two athletes who log the same prescription identically get
the same value, and that does not show they ran the intervals identically. Hence its own basis
(``prescribed_timed_work_over_elapsed``), a separate reserved basis for performed structure,
and default exclusion from calibration fitting.
"""
from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, get_args

import pytest
from sqlalchemy import select

from app.logic import dose_engine_v1
from app.logic.dose_model import DENSITY_FIT_ELIGIBILITY, DensityBasis, density_fit_eligible
from app.models.dose_model_shadow import DoseModelShadowLog
from app.schemas.workout_structure import (
    ContinuousBlock,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    WorkoutStructure,
)
from app.schemas.workouts import WorkoutLog
from app.services.dose_model_shadow_service import resolve_prescribed_density

ROOT = Path(__file__).resolve().parents[1]

#: 4 x 5 min work, 3 x 2 min recovery: 20 min of work, a 26 min block.
_INTERVALS: WorkoutStructure = [
    IntervalBlock(
        activity="Threshold Tempo Run", display_sets=4, display_reps="5 min / 2 min easy",
        repetitions=4, work_duration_sec=300, recovery_duration_sec=120,
        recovery_type="easy", intensity_basis="rpe", intensity_target=8.0,
    )
]


def _content(structure: WorkoutStructure | None) -> dict[str, Any]:
    return {"structure": None if structure is None else [b.model_dump(mode="json") for b in structure]}


def _log(minutes: float, modality: str = "Running") -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        modality=modality,  # type: ignore[arg-type]
        duration_minutes=minutes,
        session_rpe=7,
    )


# ── the paired acceptance test ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("elapsed_min", "expected", "reason"),
    [
        (26.0, 20 / 26, None),                                        # log A
        (30.0, 20 / 30, None),                                        # log B
        (18.0, None, "prescribed_work_exceeds_logged_elapsed"),       # log C
    ],
    ids=["A_26min", "B_30min", "C_18min"],
)
def test_the_same_prescription_logged_three_ways(
    elapsed_min: float, expected: float | None, reason: str | None
) -> None:
    density, provenance = resolve_prescribed_density(_log(elapsed_min), _content(_INTERVALS))

    assert density is not None and provenance is not None
    assert density.reason == reason
    if expected is None:
        assert density.value is None and density.basis == "not_applicable"
    else:
        assert density.value == pytest.approx(expected)
        assert density.basis == "prescribed_timed_work_over_elapsed"
    assert provenance["provenance"] == "prescribed_not_performed"
    # The prescribed work is known in all three; only log C's density is rejected.
    assert provenance["work_seconds"] == 1200.0


def test_identical_logs_of_one_prescription_get_one_proxy_value() -> None:
    """Same prescription, same logged time: same value. This does NOT show the two athletes
    performed the intervals identically; the proxy cannot see performance at all."""
    a, _ = resolve_prescribed_density(_log(28.0), _content(_INTERVALS))
    b, _ = resolve_prescribed_density(_log(28.0), _content(_INTERVALS))

    assert a == b


def test_work_counts_intervals_only_and_recovery_reaches_it_through_elapsed_time() -> None:
    work, reason = dose_engine_v1.prescribed_timed_work_seconds(
        [WarmupBlock(duration_sec=600), *_INTERVALS]
    )

    assert (work, reason) == (1200.0, None)  # not 1200 + 360 recovery + 600 warmup


# ── the invariant it inherits ────────────────────────────────────────────────


def test_same_work_in_more_time_is_less_dense_and_more_work_in_the_same_time_is_denser() -> None:
    d = dose_engine_v1.prescribed_work_density

    assert d(1200, 30).value < d(1200, 26).value  # type: ignore[operator]
    assert d(1500, 30).value > d(1200, 30).value  # type: ignore[operator]


# ── what stays NOT MODELLED ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("log", "content", "reason"),
    [
        (_log(40.0), _content([ContinuousBlock(activity="Easy Run", intensity_basis="zone")]),
         "structure_not_fully_timed"),                                  # "30-40 min" range
        (_log(30.0), _content([IntervalBlock(activity="Sprint", repetitions=3, work_distance_m=30.0)]),
         "structure_not_fully_timed"),                                  # distance-only
        (_log(30.0), None, "no_explicit_prescription_link"),           # unplanned / heuristic
        (_log(30.0), _content([StrengthBlock(exercise="Back Squat", sets=5)]),
         "structure_is_not_endurance"),
        (_log(0.0), _content(_INTERVALS), "missing_logged_elapsed"),   # zero is missing
        (_log(30.0), {"exercises": []}, "prescription_has_no_structure"),  # legacy stored row
        (_log(30.0), {"structure": [{"kind": "nonsense"}]}, "prescription_structure_unreadable"),
    ],
    ids=["range", "distance_only", "unlinked", "strength", "zero_elapsed", "legacy", "unreadable"],
)
def test_what_the_proxy_refuses_to_measure(
    log: WorkoutLog, content: dict[str, Any] | None, reason: str
) -> None:
    density, provenance = resolve_prescribed_density(log, content)

    assert density is not None and density.value is None
    assert density.basis == "not_applicable"
    assert density.reason == reason
    assert provenance is not None and provenance["reason"] == reason


def test_a_set_counted_session_ignores_the_prescription() -> None:
    """Reported sets are a measurement; a plan never outranks one."""
    density, provenance = resolve_prescribed_density(_log(30.0, "Strength"), _content(_INTERVALS))

    assert (density, provenance) == (None, None)
    measured = dose_engine_v1.prescribed_work_density(1200, 30)
    log = _log(30.0, "Strength")
    assert dose_engine_v1.calculate_stress_dose(log, prescribed_density=measured) == (
        dose_engine_v1.calculate_stress_dose(log)
    )


def test_v1_carries_the_proxy_and_labels_it() -> None:
    log = _log(30.0)
    density, _ = resolve_prescribed_density(log, _content(_INTERVALS))

    dose = dose_engine_v1.calculate_stress_dose(log, prescribed_density=density)

    assert dose.density_basis == "prescribed_timed_work_over_elapsed"
    assert dose.density_value == pytest.approx(20 / 30)
    assert dose.dose_model_version == "v1.1"


# ── calibration policy, encoded now ──────────────────────────────────────────


def test_every_density_basis_has_a_fit_policy() -> None:
    assert set(DENSITY_FIT_ELIGIBILITY) == set(get_args(DensityBasis))


def test_the_prescribed_proxy_is_excluded_from_fitting_by_default() -> None:
    assert not density_fit_eligible("prescribed_timed_work_over_elapsed")
    assert not density_fit_eligible("not_applicable")
    assert density_fit_eligible("performed_timed_work_over_elapsed")
    assert density_fit_eligible("sets_per_elapsed_minute")
    assert not density_fit_eligible(None)
    assert not density_fit_eligible("some_future_basis")


def test_calibration_code_reading_the_shadow_log_consults_the_fit_policy() -> None:
    """A policy nothing consults is documentation. Any module under app/ml that reads the
    dose shadow log must reference ``density_fit_eligible``. Vacuous today (no loader exists);
    it fires the day 8B adds one."""
    offenders = []
    for path in (ROOT / "app" / "ml").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        names = {
            n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(ast.parse(text))
            if isinstance(n, ast.Name | ast.Attribute)
        }
        reads_log = "DoseModelShadowLog" in names or "dose_model_shadow_log" in text
        if reads_log and "density_fit_eligible" not in names:
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, offenders


def test_every_density_basis_fits_the_shadow_column() -> None:
    """v1_density_basis is varchar(40). An over-long basis would fail the insert, and the
    best-effort writer would swallow it: rows silently lost."""
    width = DoseModelShadowLog.__table__.c.v1_density_basis.type.length
    too_long = [b for b in get_args(DensityBasis) if len(b) > width]

    assert not too_long, too_long


# ── through the real ingest path (database) ──────────────────────────────────


async def _athlete_with_planned_run(db, email: str, content: dict[str, Any]):
    from app.models.mesocycle import (
        BlockGoal,
        BlockStatus,
        MesocycleBlock,
        PlannedSession,
        SessionStatus,
    )
    from app.models.user import User

    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    block = MesocycleBlock(
        user_id=user.id, goal=BlockGoal.STRENGTH, status=BlockStatus.ACTIVE,
        duration_weeks=4, start_date=date.today(), weekly_template=[],
    )
    db.add(block)
    await db.commit()
    await db.refresh(block)
    session = PlannedSession(
        block_id=block.id, user_id=user.id, scheduled_date=date.today(), week_number=1,
        day_of_week=1, category="Threshold Work", modality="running",
        status=SessionStatus.PENDING, prescribed_content=content,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return user, session


@pytest.mark.asyncio
async def test_only_an_explicit_link_lends_the_prescription_to_v1(async_db) -> None:
    """Two athletes, the same planned run, the same log. One sends planned_session_id; the
    other is matched by the same-day heuristic. Only the explicit link feeds v1's density —
    and production v0, which drives state, is identical for both."""
    from app.models.workout_log import WorkoutLog as WorkoutLogORM
    from app.services.state_service import process_new_workout

    content = {
        "exercises": [{"name": "Threshold Tempo Run", "sets": 4, "reps": "5 min / 2 min easy"}],
        **_content(_INTERVALS),
    }
    explicit_user, explicit_session = await _athlete_with_planned_run(
        async_db, "linked-run@test.com", content
    )
    heuristic_user, _ = await _athlete_with_planned_run(async_db, "matched-run@test.com", content)
    now = datetime.now(UTC).replace(tzinfo=None)

    def log(**kw: object) -> WorkoutLog:
        return WorkoutLog(timestamp=now, modality="Running", duration_minutes=30.0,
                          session_rpe=7, **kw)  # type: ignore[arg-type]

    await process_new_workout(async_db, explicit_user.id, log(planned_session_id=explicit_session.id))
    await process_new_workout(async_db, heuristic_user.id, log())

    rows = {
        r.user_id: r
        for r in (await async_db.execute(select(DoseModelShadowLog))).scalars().all()
    }
    linked, matched = rows[explicit_user.id], rows[heuristic_user.id]

    assert linked.v1_density_basis == "prescribed_timed_work_over_elapsed"
    assert linked.v1_density_value == pytest.approx(20 / 30)
    assert linked.v1_dose_json["density_provenance"]["provenance"] == "prescribed_not_performed"
    assert matched.v1_density_basis == "not_applicable"
    assert matched.v1_dose_json["density_provenance"]["reason"] == "no_explicit_prescription_link"

    snapshots = {
        r.user_id: r.dose_snapshot
        for r in (await async_db.execute(select(WorkoutLogORM))).scalars().all()
    }
    assert snapshots[explicit_user.id] == snapshots[heuristic_user.id]
    assert snapshots[explicit_user.id]["dose_model_version"] == "v0"
