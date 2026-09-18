"""Density means work per unit elapsed time — in both directions, at both levels.

The ruling (2026-09-18) these tests enforce:

    fixed work + more elapsed time/rest  -> density DECREASES
    fixed work + less elapsed time/rest  -> density INCREASES
    fixed elapsed time + more work       -> density INCREASES
    zero/unknown time                    -> no inf/NaN, explicit neutral fallback

and the consistency claim that the whole defect came down to:

    compressing the timing of every exercise at constant work must never raise
    exercise density while lowering session density

v0 failed the last one by construction — its session Δ was minutes-per-set (longer = denser)
while its per-exercise Δ was sets-per-rest-minute (longer rest = sparser), so the two levels
disagreed about what the same session had done.

These tests drive app/logic/dose_engine_v1.py. The frozen v0 semantics are pinned separately
in tests/test_dose_engine_v0_frozen.py — deliberately NOT changed, because historical states
must stay reproducible under the engine that produced them.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.engine.parameters import default_parameters
from app.logic import dose_engine_v1 as v1
from app.schemas.workouts import ExerciseEntry, WorkoutLog

settings.register_profile("density_ci", deadline=None, max_examples=200, derandomize=True)
settings.load_profile("density_ci")

P = default_parameters()


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.0,
        "estimated_sets": 20.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _entry(sets: float, rest_seconds: float, duration_seconds: float | None = None) -> ExerciseEntry:
    return ExerciseEntry(
        exercise_name="Back Squat",
        sets=sets,
        reps=5,
        load_kg=100.0,
        rest_seconds=rest_seconds,
        duration_seconds=duration_seconds,
    )


# ── the four directional invariants ───────────────────────────────────────────

@given(
    sets=st.floats(min_value=1.0, max_value=40.0),
    duration=st.floats(min_value=10.0, max_value=120.0),
    extra=st.floats(min_value=1.0, max_value=120.0),
)
def test_same_work_more_time_lowers_density(sets: float, duration: float, extra: float) -> None:
    tighter = v1.session_density_from_parts(duration, sets, P)
    looser = v1.session_density_from_parts(duration + extra, sets, P)
    # Only meaningful where the clamp is not already binding at both ends.
    assume(tighter < P.dose_delta_cap or looser < P.dose_delta_cap)
    assume(tighter > P.dose_delta_floor or looser > P.dose_delta_floor)

    assert looser <= tighter


@given(
    sets=st.floats(min_value=4.0, max_value=30.0),
    duration=st.floats(min_value=30.0, max_value=120.0),
    shrink=st.floats(min_value=0.2, max_value=0.9),
)
def test_same_work_less_time_raises_density(sets: float, duration: float, shrink: float) -> None:
    slower = v1.session_density_from_parts(duration, sets, P)
    faster = v1.session_density_from_parts(duration * shrink, sets, P)
    assume(slower > P.dose_delta_floor or faster > P.dose_delta_floor)

    assert faster >= slower


@given(
    duration=st.floats(min_value=20.0, max_value=120.0),
    sets=st.floats(min_value=2.0, max_value=20.0),
    more=st.floats(min_value=1.0, max_value=20.0),
)
def test_same_time_more_work_raises_density(duration: float, sets: float, more: float) -> None:
    fewer = v1.session_density_from_parts(duration, sets, P)
    extra = v1.session_density_from_parts(duration, sets + more, P)
    assume(fewer < P.dose_delta_cap or extra < P.dose_delta_cap)

    assert extra >= fewer


@pytest.mark.parametrize(
    ("label", "duration", "sets"),
    [
        ("zero duration", 0.0, 12.0),
        ("zero sets", 45.0, 0.0),
        ("both zero", 0.0, 0.0),
        ("negative duration cannot reach here but is handled", -10.0, 12.0),
    ],
)
def test_unknown_time_is_an_explicit_neutral_not_a_nan(
    label: str, duration: float, sets: float
) -> None:
    """Missing information must be declared, not improvised."""
    density = v1.session_density_from_parts(duration, sets, P)

    assert math.isfinite(density), label
    assert density == v1.DENSITY_WHEN_TIME_UNKNOWN, label


@given(
    sets=st.floats(min_value=1.0, max_value=40.0),
    duration=st.floats(min_value=0.0, max_value=300.0),
)
def test_session_density_is_always_finite_and_within_the_declared_band(
    sets: float, duration: float
) -> None:
    density = v1.session_density_from_parts(duration, sets, P)

    assert math.isfinite(density)
    assert P.dose_delta_floor <= density <= P.dose_delta_cap or (
        density == v1.DENSITY_WHEN_TIME_UNKNOWN
    )


# ── the per-exercise proxy ────────────────────────────────────────────────────

@given(
    sets=st.floats(min_value=2.0, max_value=12.0),
    rest=st.floats(min_value=10.0, max_value=300.0),
    extra_rest=st.floats(min_value=5.0, max_value=300.0),
)
def test_more_rest_lowers_exercise_density(sets: float, rest: float, extra_rest: float) -> None:
    """The direction v0 got right at the exercise level — kept, and now agreeing with sessions."""
    tight = v1.exercise_density_proxy(_entry(sets, rest), P)
    rested = v1.exercise_density_proxy(_entry(sets, rest + extra_rest), P)
    assume(tight > P.dose_delta_floor or rested > P.dose_delta_floor)

    assert rested <= tight


def test_the_exercise_proxy_counts_work_as_well_as_rest() -> None:
    """``sets / rest`` is not elapsed time: the work itself occupies the clock too.

    Two exercises with identical rest but different working time must not read as equally
    dense — v0's per-exercise Δ said they were, because it never looked at the work.
    """
    quick = _entry(5, rest_seconds=90.0, duration_seconds=100.0)
    slow = _entry(5, rest_seconds=90.0, duration_seconds=400.0)

    assert v1.exercise_density_proxy(quick, P) > v1.exercise_density_proxy(slow, P)


def test_an_exercise_with_no_timing_falls_back_explicitly() -> None:
    bare = ExerciseEntry(exercise_name="Back Squat", sets=4, reps=5)

    assert v1.estimated_exercise_elapsed_minutes(bare) is None
    assert v1.exercise_density_proxy(bare, P) == v1.DENSITY_WHEN_TIME_UNKNOWN


# ── the consistency claim that started this ───────────────────────────────────

def test_compressing_every_exercise_moves_both_levels_the_same_way() -> None:
    """The contradiction that triggered the whole issue, stated as a test.

    Same work — same sets, same reps, same load — performed in less total time. Exercise
    density must rise, and session density must NOT fall. Under v0 these disagreed: the
    session read *sparser* (fewer minutes per set) while each exercise read *denser*.
    """
    sets_per_exercise = 5.0
    exercises_in_session = 4
    total_sets = sets_per_exercise * exercises_in_session

    relaxed_entry = _entry(sets_per_exercise, rest_seconds=180.0, duration_seconds=150.0)
    compressed_entry = _entry(sets_per_exercise, rest_seconds=60.0, duration_seconds=150.0)

    relaxed_minutes = v1.estimated_exercise_elapsed_minutes(relaxed_entry)
    compressed_minutes = v1.estimated_exercise_elapsed_minutes(compressed_entry)
    assert relaxed_minutes is not None and compressed_minutes is not None
    assert compressed_minutes < relaxed_minutes, "precondition: the session really is shorter"

    relaxed_exercise = v1.exercise_density_proxy(relaxed_entry, P)
    compressed_exercise = v1.exercise_density_proxy(compressed_entry, P)
    relaxed_session = v1.session_density_from_parts(relaxed_minutes * exercises_in_session, total_sets, P)
    compressed_session = v1.session_density_from_parts(
        compressed_minutes * exercises_in_session, total_sets, P
    )

    assert compressed_exercise > relaxed_exercise, "exercise density must rise"
    assert compressed_session > relaxed_session, "session density must rise with it"


@given(
    sets=st.floats(min_value=2.0, max_value=10.0),
    rest=st.floats(min_value=30.0, max_value=240.0),
    work=st.floats(min_value=60.0, max_value=300.0),
    squeeze=st.floats(min_value=0.2, max_value=0.9),
)
def test_the_two_levels_never_disagree_about_compression(
    sets: float, rest: float, work: float, squeeze: float
) -> None:
    """Generalized: whatever the timings, the two levels move together or not at all."""
    relaxed = _entry(sets, rest_seconds=rest, duration_seconds=work)
    compressed = _entry(sets, rest_seconds=rest * squeeze, duration_seconds=work)

    relaxed_minutes = v1.estimated_exercise_elapsed_minutes(relaxed)
    compressed_minutes = v1.estimated_exercise_elapsed_minutes(compressed)
    assert relaxed_minutes is not None and compressed_minutes is not None

    exercise_delta = v1.exercise_density_proxy(compressed, P) - v1.exercise_density_proxy(
        relaxed, P
    )
    session_delta = v1.session_density_from_parts(compressed_minutes, sets, P) - v1.session_density_from_parts(
        relaxed_minutes, sets, P
    )

    assert exercise_delta * session_delta >= 0.0, (
        f"levels disagreed: exercise {exercise_delta:+.4f} vs session {session_delta:+.4f}"
    )


# ── the dose law still behaves ────────────────────────────────────────────────

@given(
    duration=st.floats(min_value=0.0, max_value=300.0),
    sets=st.floats(min_value=1.0, max_value=40.0),
    rpe=st.floats(min_value=1.0, max_value=10.0),
)
def test_v1_dose_axes_stay_finite_and_non_negative(
    duration: float, sets: float, rpe: float
) -> None:
    dose = v1.calculate_stress_dose(
        _log(duration_minutes=duration, estimated_sets=sets, session_rpe=rpe)
    )

    for name, value in dose.dose_six.model_dump().items():
        assert math.isfinite(value) and value >= 0.0, f"{name}={value}"


def test_a_compressed_session_carries_more_dose_than_a_drawn_out_one() -> None:
    """The end-to-end consequence, at equal work: density is the only thing differing."""
    quick = v1.calculate_stress_dose(_log(duration_minutes=45.0, estimated_sets=20.0))
    drawn_out = v1.calculate_stress_dose(_log(duration_minutes=90.0, estimated_sets=20.0))

    assert quick.dose_six.density > drawn_out.dose_six.density


# ── what counts as work, and when we admit we do not know ─────────────────────

def test_a_continuous_run_does_not_get_a_density_derived_from_fabricated_sets() -> None:
    """The bug the first draft of v1 shipped, pinned so it cannot come back.

    v0 fabricates ``sets = max(3, duration/12)`` when a log reports none. Measuring
    sets-per-minute against that invention reported a 60-minute run as nearly empty, which
    made a running plan LOSE aerobic capacity in tests/test_projection.py. A continuous
    effort's work is distance and pace; until endurance has a real target (phase 5), the
    honest density for it is the declared neutral.
    """
    run = _log(modality="Running", duration_minutes=60.0, estimated_sets=None)

    assert v1.session_density(run, 5.0, P) == v1.DENSITY_WHEN_TIME_UNKNOWN


def test_a_strength_session_without_reported_sets_is_also_neutral() -> None:
    """Same principle, same modality: a fallback set count is not measured work."""
    unreported = _log(modality="Strength", duration_minutes=60.0, estimated_sets=None)

    assert v1.session_density(unreported, 12.0, P) == v1.DENSITY_WHEN_TIME_UNKNOWN


def test_a_strength_session_with_reported_sets_uses_them() -> None:
    reported = _log(modality="Strength", duration_minutes=40.0, estimated_sets=20.0)
    slower = _log(modality="Strength", duration_minutes=90.0, estimated_sets=20.0)

    assert v1.session_density(reported, 20.0, P) > v1.session_density(slower, 20.0, P)


@pytest.mark.parametrize("modality", ["Strength", "Hypertrophy", "Power"])
def test_set_counted_modalities_are_the_ones_that_report_sets(modality: str) -> None:
    dense = _log(modality=modality, duration_minutes=30.0, estimated_sets=18.0)
    sparse = _log(modality=modality, duration_minutes=110.0, estimated_sets=18.0)

    assert v1.session_density(dense, 18.0, P) > v1.session_density(sparse, 18.0, P)


@pytest.mark.parametrize("modality", ["Running", "Mixed"])
def test_non_set_counted_modalities_stay_neutral_whatever_the_timing(modality: str) -> None:
    quick = _log(modality=modality, duration_minutes=20.0, estimated_sets=10.0)
    long = _log(modality=modality, duration_minutes=120.0, estimated_sets=10.0)

    assert v1.session_density(quick, 10.0, P) == v1.DENSITY_WHEN_TIME_UNKNOWN
    assert v1.session_density(long, 10.0, P) == v1.DENSITY_WHEN_TIME_UNKNOWN
