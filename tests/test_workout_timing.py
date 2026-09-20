"""Time semantics for workout structure (phase 2.2) — descriptive, never prescriptive.

Calculated duration explains a session. It does not rewrite ``duration_min``, move a dose or
change a selection; the acceptance gate for that lives in
tests/test_structure_projection_identity.py and compares against the pre-2.1 committed matrix.

What these tests pin is the arithmetic and, more importantly, the honesty: an untimed session
reports NOTHING rather than a partial sum dressed up as a duration.
"""
import pytest
from pydantic import ValidationError

from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.schemas.workout_structure import (
    ContinuousBlock,
    CooldownBlock,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    calculate_duration,
)


def test_a_continuous_block_is_its_own_duration() -> None:
    assert calculate_duration([ContinuousBlock(duration_sec=1800)]).minutes == 30.0


# ── the last-recovery rule, stated explicitly ────────────────────────────────

def test_intervals_exclude_the_final_recovery_by_default() -> None:
    """4 × 5 min work + 3 × 2 min recovery = 26 min. The work is the session."""
    block = IntervalBlock(repetitions=4, work_duration_sec=300, recovery_duration_sec=120)

    assert calculate_duration([block]).minutes == 26.0


def test_intervals_include_the_final_recovery_when_the_block_says_so() -> None:
    """The same block, recovering after the last rep, is 28 min — the classic ambiguity."""
    block = IntervalBlock(
        repetitions=4, work_duration_sec=300, recovery_duration_sec=120,
        recovery_after_last_rep=True,
    )

    assert calculate_duration([block]).minutes == 28.0


def test_strength_rest_follows_the_same_rule() -> None:
    """3 sets, 120 s rest: two gaps by default, three if rest follows the last set."""
    timed = StrengthBlock(exercise="Back Squat", sets=3, set_duration_sec=30, rest_sec=120)

    assert calculate_duration([timed]).known_seconds == 3 * 30 + 2 * 120
    after = timed.model_copy(update={"rest_after_last_set": True})
    assert calculate_duration([after]).known_seconds == 3 * 30 + 3 * 120


# ── additivity ───────────────────────────────────────────────────────────────

def test_warmup_main_and_cooldown_add_up() -> None:
    structure = [
        WarmupBlock(duration_sec=600),
        ContinuousBlock(duration_sec=1800),
        CooldownBlock(duration_sec=300),
    ]

    assert calculate_duration(structure).minutes == 45.0


def test_transitions_count_once_per_block_and_are_not_recovery() -> None:
    """A station-to-run transition is not prescribed recovery — HYROX needs them apart."""
    without = IntervalBlock(repetitions=2, work_duration_sec=60, recovery_duration_sec=30)
    with_transition = without.model_copy(update={"transition_sec": 45})

    assert calculate_duration([without]).known_seconds == 2 * 60 + 30
    assert calculate_duration([with_transition]).known_seconds == 2 * 60 + 30 + 45


# ── unknown is not zero ──────────────────────────────────────────────────────

def test_an_untimed_strength_block_is_incomplete_not_zero() -> None:
    """Sets and reps without execution time cannot be timed, and must not pretend otherwise."""
    estimate = calculate_duration(
        [StrengthBlock(exercise="Back Squat", sets=4, reps="5", rest_sec=180)]
    )

    assert estimate.minutes is None, "an incomplete estimate must not render as minutes"
    assert estimate.complete is False
    assert estimate.known_seconds == 3 * 180, "the known rest is still reported"
    assert estimate.unknown_components == ["Back Squat: set execution time unknown"]


def test_distance_only_work_is_not_a_time_quantity() -> None:
    """5 × 1 km has no duration until a pace target exists — phase 5, not an assumed speed."""
    intervals = calculate_duration(
        [IntervalBlock(label="1k reps", repetitions=5, work_distance_m=1000.0)]
    )
    steady = calculate_duration([ContinuousBlock(label="Long run", distance_m=15000.0)])

    assert intervals.minutes is None and steady.minutes is None
    assert "no pace target" in intervals.unknown_components[0]
    assert "no pace target" in steady.unknown_components[0]


def test_a_partially_known_session_reports_both_halves() -> None:
    """The known part is preserved AND the missing part is named — neither is discarded."""
    estimate = calculate_duration(
        [
            WarmupBlock(duration_sec=600),
            StrengthBlock(exercise="Deadlift", sets=3, reps="5", rest_sec=180),
        ]
    )

    assert estimate.known_seconds == 600 + 2 * 180
    assert estimate.minutes is None
    assert estimate.unknown_components == ["Deadlift: set execution time unknown"]


@pytest.mark.parametrize(
    "block",
    [
        lambda: ContinuousBlock(duration_sec=-1),
        lambda: IntervalBlock(repetitions=-2),
        lambda: IntervalBlock(recovery_duration_sec=-30),
        lambda: StrengthBlock(exercise="Back Squat", rest_sec=-5),
        lambda: WarmupBlock(duration_sec=-600),
        lambda: ContinuousBlock(transition_sec=-10),
    ],
)
def test_negative_time_is_refused_at_the_boundary(block) -> None:
    with pytest.raises(ValidationError):
        block()


# ── what the prescription exposes ────────────────────────────────────────────

def test_the_prescription_reports_calculated_duration_without_touching_the_prescribed_one() -> None:
    rx = WorkoutPrescription(
        type="Max Strength", focus="f", rationale="r", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=4, reps="5")],
    ).with_structure()

    assert rx.duration_min == 60, "the prescribed duration is untouched"
    assert rx.calculated_duration_min is None, "and the structure admits it cannot time itself"
    assert rx.duration_estimate is not None and not rx.duration_estimate.complete


def test_a_fully_timed_structure_reports_its_minutes() -> None:
    rx = WorkoutPrescription(
        type="Threshold", focus="f", rationale="r", duration_min=45,
        structure=[
            WarmupBlock(duration_sec=600),
            IntervalBlock(repetitions=4, work_duration_sec=300, recovery_duration_sec=120),
            CooldownBlock(duration_sec=300),
        ],
    )

    assert rx.calculated_duration_min == 41.0
    assert rx.duration_min == 45, "still descriptive: the prescribed value does not move"


def test_calculated_duration_cannot_drift_from_the_structure() -> None:
    """It is computed, not stored — there is no field to set inconsistently."""
    rx = WorkoutPrescription(
        type="Threshold", focus="f", rationale="r", duration_min=45,
        structure=[ContinuousBlock(duration_sec=1200)],
    )
    assert rx.calculated_duration_min == 20.0

    longer = rx.model_copy(update={"structure": [ContinuousBlock(duration_sec=2400)]})
    assert longer.calculated_duration_min == 40.0
