"""Candidate strength difficulty policies (phase 3.2) — what they do, and that they are not live.

Three families, three different meanings of "harder", none of them in the prescription path.
These tests pin the semantics the reports describe, so a report cannot drift from the code
that made it.

**3.4 decided: none promoted** (`docs/simulations/phase-3-4.md`). The promotion evidence is
that report, NOT `phase-3-2.md` — 3.2 compared each candidate against a bare baseline rather
than against the live rule, which moves effort as well as sets. These tests describe what the
candidates do; they take no position on whether the candidates should be live.
"""
import pytest

from app.logic.difficulty import POLICIES, DifficultyDimension, dimensions_changed, violations
from app.logic.difficulty_strength import (
    CANDIDATE_TRANSFORMS,
    GENERAL_STRENGTH,
    HYPERTROPHY,
    MAX_STRENGTH,
)
from app.schemas.workout_structure import StrengthBlock, WarmupBlock

FAMILIES = pytest.mark.parametrize(
    "transform", [GENERAL_STRENGTH, HYPERTROPHY, MAX_STRENGTH], ids=lambda t: t.family
)


def _baseline() -> list:
    return [
        StrengthBlock(exercise="Back Squat", sets=5, reps="5", rpe_target=8.0, rest_sec=180),
        StrengthBlock(exercise="Romanian Deadlift", sets=4, reps="8", rpe_target=8.0, rest_sec=120),
        WarmupBlock(duration_sec=600),
    ]


def _sets(structure) -> int:
    return sum(b.sets or 0 for b in structure if isinstance(b, StrengthBlock))


# ── the anchor ───────────────────────────────────────────────────────────────

@FAMILIES
def test_medium_is_the_exact_identity(transform) -> None:
    """Not "equivalent after rounding" — the same structure, so every family has a fixed point."""
    baseline = _baseline()

    assert transform.apply(baseline, "medium") == baseline


@FAMILIES
def test_an_unrecognized_level_falls_back_to_medium(transform) -> None:
    baseline = _baseline()

    assert transform.apply(baseline, "nonsense") == baseline


# ── volume vs effort ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("transform", [GENERAL_STRENGTH, HYPERTROPHY], ids=lambda t: t.family)
def test_volume_families_order_by_set_count(transform) -> None:
    baseline = _baseline()

    easy = _sets(transform.apply(baseline, "easy"))
    medium = _sets(baseline)
    hard = _sets(transform.apply(baseline, "hard"))

    assert easy < medium < hard


def test_hypertrophy_moves_volume_further_than_general_strength() -> None:
    """Accumulated work is hypertrophy's primary driver, so its hard end reaches higher."""
    baseline = _baseline()

    assert _sets(HYPERTROPHY.apply(baseline, "hard")) >= _sets(
        GENERAL_STRENGTH.apply(baseline, "hard")
    )


def test_max_strength_is_not_ordered_by_set_count() -> None:
    """A harder maximal session can carry FEWER total reps at a heavier load.

    Forcing easy < medium < hard on sets would encode the wrong model of the session: its
    difficulty is proximity to failure and the load that follows from it.
    """
    baseline = _baseline()

    assert _sets(MAX_STRENGTH.apply(baseline, "hard")) == _sets(baseline)
    assert _sets(MAX_STRENGTH.apply(baseline, "easy")) < _sets(baseline)


def test_max_strength_hardens_by_effort_alone() -> None:
    baseline = _baseline()
    hard = MAX_STRENGTH.apply(baseline, "hard")

    assert dimensions_changed(baseline, hard) == {DifficultyDimension.EFFORT}
    assert hard[0].rpe_target == 9.0, "one rep in reserve closer to failure"


@FAMILIES
def test_harder_always_means_closer_to_failure(transform) -> None:
    baseline = _baseline()

    easy = transform.apply(baseline, "easy")[0]
    hard = transform.apply(baseline, "hard")[0]

    assert easy.rpe_target == 7.0 and hard.rpe_target == 9.0


# ── effort moves; load is derived ────────────────────────────────────────────

@FAMILIES
@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_a_transform_never_sets_load_itself(transform, level) -> None:
    """Load follows from effort downstream. Setting both would double-count one change."""
    baseline = _baseline()

    for block in transform.apply(baseline, level):
        if isinstance(block, StrengthBlock):
            assert block.load_target_kg is None
            assert block.percent_e1rm is None


def test_effort_is_expressed_in_whichever_field_the_block_carries() -> None:
    """RIR and RPE are the same statement from opposite ends: RIR = 10 − RPE."""
    by_rir = [StrengthBlock(exercise="Squat", sets=5, reps="5", rir_target=2.0)]
    by_rpe = [StrengthBlock(exercise="Squat", sets=5, reps="5", rpe_target=8.0)]

    assert GENERAL_STRENGTH.apply(by_rir, "hard")[0].rir_target == 1.0
    assert GENERAL_STRENGTH.apply(by_rpe, "hard")[0].rpe_target == 9.0
    assert GENERAL_STRENGTH.apply(by_rir, "easy")[0].rir_target == 3.0
    assert GENERAL_STRENGTH.apply(by_rpe, "easy")[0].rpe_target == 7.0


def test_a_block_stating_no_effort_target_is_not_given_one() -> None:
    """Inventing a target would be fabricating a prescription the template never made."""
    bare = [StrengthBlock(exercise="Squat", sets=5, reps="5")]
    hardened = GENERAL_STRENGTH.apply(bare, "hard")

    assert hardened[0].rpe_target is None and hardened[0].rir_target is None
    assert hardened[0].sets == 6, "volume still moves"


@FAMILIES
@pytest.mark.parametrize("level", ["easy", "hard"])
def test_no_candidate_touches_rest(transform, level) -> None:
    """Density is deliberately out of the first candidates — one fewer knob to attribute."""
    baseline = _baseline()
    after = transform.apply(baseline, level)

    assert DifficultyDimension.DENSITY not in dimensions_changed(baseline, after)


@FAMILIES
@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_warmups_are_never_touched(transform, level) -> None:
    baseline = _baseline()

    assert transform.apply(baseline, level)[-1] == baseline[-1]


# ── each candidate obeys its own declared policy ─────────────────────────────

@FAMILIES
@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_every_candidate_respects_its_family_policy(transform, level) -> None:
    baseline = _baseline()
    policy = POLICIES[transform.family]

    assert violations(baseline, transform.apply(baseline, level), policy) == []


def test_every_candidate_family_has_a_declared_policy() -> None:
    assert set(CANDIDATE_TRANSFORMS) <= set(POLICIES)


# ── not live ─────────────────────────────────────────────────────────────────

def test_the_candidates_are_not_wired_into_the_prescription_path() -> None:
    """3.2 produces evidence, not behaviour. Promotion is 3.4, from measured consequences."""
    from pathlib import Path

    prescriber = (
        Path(__file__).resolve().parents[1] / "app/logic/prescriber.py"
    ).read_text(encoding="utf-8")

    assert "difficulty_strength" not in prescriber
    assert "LEGACY_TRANSFORM" in prescriber, "the live path still uses the legacy rule"
