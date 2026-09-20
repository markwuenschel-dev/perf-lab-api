"""Difficulty invariants (phase 3.3): what a transform may change, and how far.

Four classes of invariant, deliberately kept apart because they fail for different reasons:

1. **Permission** — a family's forbidden dimensions never move.
2. **Intent** — a transform moves only what it declared. Volume-only must not touch effort;
   effort-only must not add sets.
3. **Anchor** — medium is exact identity, exercise selection never changes, and the two
   families whose sessions are defined by full recovery never have it shortened.
4. **Controlled monotonicity** — ONLY where the transformation isolates one variable: more
   sets at an otherwise identical prescription is more v1 dose; a deload is not more v1 dose.

**Scalar dose ordering is not a universal invariant.** Phase 3.2 measured max strength at
hard/easy = 1.02× in BOTH engines: a real increase in resolved load that the dose law barely
values. Demanding "hard must out-dose easy" everywhere would encode the dose law's current
insensitivity as a requirement on prescriptions. That insensitivity is recorded as a
calibration finding (docs/calibration-backlog.md) and asserted here only structurally — the
RIR falls, the resolved load rises — not as a magnitude.

The validator itself is tested with intentionally illegal transforms, each of which must fail
for its own stated reason rather than producing a generic "policy violation".
"""
import pytest

from app.logic import dose_engine_v1 as v1
from app.logic import strength_calibration as sc
from app.logic.difficulty import (
    POLICIES,
    DifficultyDimension,
    Permission,
    constraint_for,
    dimensions_changed,
    validate_transform,
)
from app.logic.difficulty_strength import (
    CANDIDATE_TRANSFORMS,
    GENERAL_STRENGTH,
    MAX_STRENGTH,
)
from app.schemas.workout_structure import StrengthBlock, WarmupBlock
from app.schemas.workouts import WorkoutLog

LEVELS = ("easy", "medium", "hard")
VOLUME_ONLY = frozenset({DifficultyDimension.VOLUME})
EFFORT_ONLY = frozenset({DifficultyDimension.EFFORT})


def _baseline() -> list:
    return [
        StrengthBlock(exercise="Back Squat", sets=5, reps="5", rpe_target=8.0, rest_sec=180),
        StrengthBlock(exercise="Romanian Deadlift", sets=4, reps="8", rpe_target=8.0, rest_sec=120),
        WarmupBlock(duration_sec=600),
    ]


# ── 1. permission ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family", sorted(CANDIDATE_TRANSFORMS))
@pytest.mark.parametrize("level", LEVELS)
def test_no_candidate_moves_a_forbidden_dimension(family: str, level: str) -> None:
    baseline = _baseline()
    transform = CANDIDATE_TRANSFORMS[family]

    assert validate_transform(
        baseline, transform.apply(baseline, level),
        family=family, declared=transform.declared_dimensions,
    ) == []


def test_a_forbidden_dimension_fails_by_name() -> None:
    """max strength must never have its rest shortened — that is a different session."""
    baseline = _baseline()
    shortened = [baseline[0].model_copy(update={"rest_sec": 60}), *baseline[1:]]

    problems = validate_transform(baseline, shortened, family="max_strength")

    assert problems == ["max_strength must not change density"]


# ── 2. intent ────────────────────────────────────────────────────────────────

def test_a_volume_only_transform_may_not_move_effort() -> None:
    baseline = _baseline()
    both = [baseline[0].model_copy(update={"sets": 6, "rpe_target": 9.0}), *baseline[1:]]

    problems = validate_transform(baseline, both, family="strength", declared=VOLUME_ONLY)

    assert len(problems) == 1
    assert "moved effort, which this transform did not declare" in problems[0]


def test_an_effort_only_transform_may_not_add_volume() -> None:
    baseline = _baseline()
    both = [baseline[0].model_copy(update={"sets": 7, "rpe_target": 9.0}), *baseline[1:]]

    problems = validate_transform(baseline, both, family="strength", declared=EFFORT_ONLY)

    assert len(problems) == 1
    assert "moved volume, which this transform did not declare" in problems[0]


def test_declaring_a_dimension_does_not_excuse_a_forbidden_one() -> None:
    """Permission is checked before intent: declaring something illegal does not legalise it."""
    baseline = _baseline()
    swapped = [baseline[0].model_copy(update={"exercise": "Front Squat"}), *baseline[1:]]

    problems = validate_transform(
        baseline, swapped, family="strength",
        declared=frozenset({DifficultyDimension.EXERCISE_SELECTION}),
    )

    assert problems == ["strength must not change exercise_selection"]


# ── 3. anchors ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("family", sorted(CANDIDATE_TRANSFORMS))
def test_medium_is_exact_identity_for_every_family(family: str) -> None:
    baseline = _baseline()

    assert CANDIDATE_TRANSFORMS[family].apply(baseline, "medium") == baseline


@pytest.mark.parametrize("family", sorted(POLICIES))
def test_no_family_may_change_exercise_selection(family: str) -> None:
    assert POLICIES[family].forbids(DifficultyDimension.EXERCISE_SELECTION)


@pytest.mark.parametrize("family", ["max_strength", "max_velocity_sprint"])
def test_full_recovery_families_never_have_recovery_shortened(family: str) -> None:
    """Both are defined by complete recovery; shortening it lowers the quality they train."""
    assert POLICIES[family].forbids(DifficultyDimension.DENSITY)


def test_a_constrained_dimension_with_no_stated_bound_may_not_move() -> None:
    """"We have not decided how far" is not permission to move any distance."""
    assert POLICIES["threshold"].permission(DifficultyDimension.DENSITY) is Permission.CONSTRAINED
    assert constraint_for("threshold", DifficultyDimension.DENSITY) is None


def test_a_constrained_dimension_is_accepted_inside_its_bound_and_rejected_outside() -> None:
    baseline = _baseline()
    inside = [baseline[0].model_copy(update={"rest_sec": 150}), *baseline[1:]]
    outside = [baseline[0].model_copy(update={"rest_sec": 60}), *baseline[1:]]

    assert validate_transform(baseline, inside, family="strength") == []
    problems = validate_transform(baseline, outside, family="strength")
    assert len(problems) == 1 and "outside its bound" in problems[0]


# ── 4. controlled monotonicity (v1 only, one variable isolated) ──────────────
#
# v0 is not asserted: its density is minutes-per-set, so it inverts these by construction —
# measured and pinned elsewhere. These say what the CORRECTED model must do.


def _dose(structure, *, duration_min: float = 75.0) -> float:
    sets = sum(b.sets or 0 for b in structure if isinstance(b, StrengthBlock))
    log = WorkoutLog(
        timestamp=__import__("datetime").datetime(2026, 9, 20, tzinfo=__import__("datetime").UTC),
        modality="Strength", duration_minutes=duration_min, session_rpe=7.5,
        estimated_sets=float(sets) if sets else None, sleep_quality=7.0, life_stress_inverse=7.0,
    )
    return float(sum(v1.calculate_stress_dose(log).dose_six.model_dump().values()))


def test_more_sets_at_an_otherwise_identical_prescription_is_more_v1_dose() -> None:
    """One variable isolated: same session, same duration, more work."""
    baseline = _baseline()
    heavier = GENERAL_STRENGTH.apply(baseline, "hard")

    assert dimensions_changed(baseline, heavier) >= {DifficultyDimension.VOLUME}
    assert _dose(heavier) > _dose(baseline)


def test_a_deload_is_never_more_v1_dose() -> None:
    """Less work in the same session must not record as more training stress."""
    baseline = _baseline()
    deloaded = GENERAL_STRENGTH.apply(baseline, "easy")

    assert _dose(deloaded) < _dose(baseline)


# ── the max-strength finding: structure asserted, magnitude recorded ─────────

def test_hard_max_strength_is_closer_to_failure_at_a_heavier_load() -> None:
    """The structural claim. The dose law's ability to VALUE it is a separate layer."""
    baseline = _baseline()
    hard = MAX_STRENGTH.apply(baseline, "hard")

    before_block, after_block = baseline[0], hard[0]
    assert after_block.rpe_target > before_block.rpe_target, "closer to failure"
    assert sc.suggested_load_kg(140.0, 5, after_block.rpe_target) > sc.suggested_load_kg(
        140.0, 5, before_block.rpe_target
    ), "resolved load rises"
    assert after_block.rest_sec == before_block.rest_sec, "rest unchanged"
    assert after_block.exercise == before_block.exercise, "selection unchanged"
    assert after_block.sets == before_block.sets, "volume unchanged — this is not a set change"


def test_the_model_barely_values_that_change_and_that_is_recorded_not_asserted() -> None:
    """Diagnostic, not a gate: no evidence exists for what the magnitude SHOULD be.

    Phase 3.2 measured hard/easy = 1.02x in both engines for max strength. This records the
    insensitivity so it cannot be forgotten, without inventing a threshold like 1.10x.
    """
    baseline = _baseline()
    easy = MAX_STRENGTH.apply(baseline, "easy")
    hard = MAX_STRENGTH.apply(baseline, "hard")

    ratio = _dose(hard) / _dose(easy)

    assert ratio > 0, "sanity"
    # Deliberately loose: the point is that it is CLOSE TO ONE, i.e. the law barely values a
    # real intensity change. Tightening this into a requirement would encode today's
    # insensitivity as intended behaviour — see docs/calibration-backlog.md.
    assert ratio < 2.0
