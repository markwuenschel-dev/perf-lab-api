"""Every scoring axis must mean what it says it means (phase 0 harness).

``SessionCandidate`` declares its axes as "0–1 each, higher = better"
(app/logic/constraint_engine/candidate.py:32-42) and ``score_candidate`` (:169-180) is a
linear weighted sum over them. Three things break that contract today:

* **A 0–1 penalty can reach 1.8.** The data-driven scorer divides the SUM of the tissue axes
  by 100, not by ``100 × len(axes)`` (app/logic/candidate_library.py:925-927), so a template
  naming three axes with ``tissue_weight=0.6`` peaks at 1.8. The gymnastics template
  hand-compensates with ``tissue_weight=1.0/3.0`` (:648) — proof that the convention is
  understood and unenforced rather than absent.
* **The weightlifting scorer adds two /100 terms** (:1026), reaching 2.0, where its siblings
  divide by 200 (:962, :994, :1059).
* **Omitting a field is not neutral.** ``goal_alignment`` and ``state_fit`` default to **1.0**
  (candidate.py:32-33) and ``score_candidate`` reads them with ``getattr(..., 0.0)`` (:177),
  so a candidate that simply does not set them collects 0.30 + 0.25 = 0.55 of score for free.
  A default should be the absence of a claim, not a maximal one.

Physiological assumption under test: none. These are measurement hygiene — a penalty summed
across three tissues must not outweigh the same penalty on one tissue merely because more
axes were listed.
"""
from __future__ import annotations

import pytest

from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY
from app.logic.constraint_engine.candidate import (
    DEFAULT_SCORE_WEIGHTS,
    SessionCandidate,
    score_candidate,
)

#: The axes candidate.py:32-42 declares as normalized 0–1.
NORMALIZED_AXES = (
    "goal_alignment",
    "state_fit",
    "fatigue_penalty",
    "tissue_penalty",
    "novelty_bonus",
    "habit_bonus",
    "weak_point_coverage",
)


def _candidate(**kwargs) -> SessionCandidate:
    defaults = {
        "type": "Test Session",
        "focus": "Test focus",
        "rationale": "Test rationale",
        "duration_min": 45,
        "branch_id": "test_branch",
    }
    defaults.update(kwargs)
    return SessionCandidate(**defaults)


def _every_template():
    seen = set()
    for pool in GOAL_TEMPLATE_LIBRARY.values():
        for template in pool:
            if id(template) not in seen:
                seen.add(id(template))
                yield template


# ── declared ranges ───────────────────────────────────────────────────────────

def test_a_summed_tissue_penalty_cannot_exceed_a_single_axis_penalty() -> None:
    """Listing more tissues must not multiply the penalty.

    Measured on the engine, not recomputed here: an athlete with EVERY tissue axis at 100 is
    scored against every template, and no penalty may exceed its declared 0-1 ceiling. (The
    phase-0 version of this test re-derived the old summed formula in its own arithmetic,
    which is the thing a test must not do — it would keep passing if the engine drifted.)
    """
    state = _athlete(0.0, 100.0, 0.5)
    offenders = [
        f"{template.branch_id}: {score_template(template, state, {}).tissue_penalty:.2f}"
        for template in _every_template()
        if score_template(template, state, {}).tissue_penalty > 1.0
    ]

    assert not offenders, "tissue penalties above the declared 0-1 range: " + "; ".join(offenders)


@pytest.mark.parametrize("axis", NORMALIZED_AXES)
def test_the_weighted_score_stays_in_zero_one(axis: str) -> None:
    """Whatever an axis carries, the final score is clamped (holds today — keep it)."""
    score = score_candidate(_candidate(**{axis: 5.0}))

    assert 0.0 <= score <= 1.0


# ── omission invariance ───────────────────────────────────────────────────────

def test_omitting_an_axis_is_neutral_rather_than_maximal() -> None:
    """A candidate making no claim must not outscore one making a modest claim."""
    silent = _candidate()
    modest = _candidate(goal_alignment=0.5, state_fit=0.5)

    assert score_candidate(silent) <= score_candidate(modest), (
        f"silent={score_candidate(silent):.3f} beat modest={score_candidate(modest):.3f}"
    )


def test_score_is_unchanged_by_fields_the_scorer_does_not_weight() -> None:
    """Setting an unweighted field must not move the score.

    ``source`` and ``duration_min`` carry no weight in DEFAULT_SCORE_WEIGHTS, so two
    candidates differing only in those must score identically — and a candidate that omits
    *weighted* axes must score as if they were neutral (0.0), not maximal.
    """
    explicit_neutral = _candidate(goal_alignment=0.0, state_fit=0.0)
    omitted = _candidate()

    assert score_candidate(omitted) == pytest.approx(score_candidate(explicit_neutral))


def test_unweighted_fields_do_not_move_the_score() -> None:
    """Holds today — pinned so the weight table stays the only thing that scores."""
    a = _candidate(goal_alignment=0.6, state_fit=0.4, duration_min=30, source="generator")
    b = _candidate(goal_alignment=0.6, state_fit=0.4, duration_min=90, source="redirect")

    assert score_candidate(a) == pytest.approx(score_candidate(b))


# ── weight table hygiene ──────────────────────────────────────────────────────

def test_penalty_axes_carry_negative_weight_and_bonuses_positive() -> None:
    """A "penalty" that adds to the score would be a sign error, not a preference."""
    for axis, weight in DEFAULT_SCORE_WEIGHTS.items():
        if axis.endswith("_penalty"):
            assert weight < 0.0, f"{axis} is a penalty with weight {weight}"
        else:
            assert weight > 0.0, f"{axis} is a bonus with weight {weight}"


def test_every_weighted_axis_exists_on_the_candidate() -> None:
    """A weight naming a field that does not exist scores getattr's default forever."""
    candidate = _candidate()

    for axis in DEFAULT_SCORE_WEIGHTS:
        assert hasattr(candidate, axis), f"weight table names unknown axis {axis!r}"


# ── every template, every athlete state (phase 1.5) ──────────────────────────
#
# The declared-range claim is universal, so it is generated rather than sampled: every
# template in the library, scored against athlete states spanning the full 0-100 fatigue and
# tissue range. Since phase 4.2 every template is spec-scored, so this and the per-template
# worst case above cover the same library.

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from app.logic.candidate_library import score_template  # noqa: E402

settings.register_profile("scoring_ci", deadline=None, max_examples=60, derandomize=True)

_AXIS = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)


def _athlete(fatigue: float, tissue: float, habit: float):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_prescriber_candidates import _state

    s = _state(
        cns=fatigue, muscular=fatigue, metabolic=fatigue, structural=fatigue, tendon=fatigue,
        lumbar=tissue, knee=tissue, habit=habit,
    )
    for axis in s.tissue_t.KEYS:
        setattr(s.tissue_t, axis, tissue)
    s.fatigue_f.grip = fatigue
    return s


@settings(settings.get_profile("scoring_ci"))
@given(fatigue=_AXIS, tissue=_AXIS, habit=st.floats(min_value=0.0, max_value=1.0))
def test_every_scored_axis_of_every_template_stays_in_its_declared_range(
    fatigue: float, tissue: float, habit: float
) -> None:
    state = _athlete(fatigue, tissue, habit)
    offenders: list[str] = []
    for template in _every_template():
        candidate = score_template(template, state, {})
        for axis in NORMALIZED_AXES:
            value = getattr(candidate, axis)
            if not 0.0 <= value <= 1.0:
                offenders.append(f"{template.branch_id}.{axis}={value:.3f}")

    assert not offenders, "axes outside their declared 0-1 range: " + ", ".join(offenders[:12])


def test_one_overloaded_tissue_is_not_diluted_by_healthy_ones() -> None:
    """Weakest link: a knee at 90 must read as a 0.9-grade penalty whatever else is listed.

    Averaging across axes would dilute it (90 with two healthy axes → 30); summing would
    inflate it past 1. The penalty tracks the most-stressed tissue the template names.

    Holds for templates declaring ``tissue_aggregate="max"``. The 13 that still average were
    migrated score-identically in 4.2; whether they move to "max" is decision 4.2b, and this
    filter is the line that decision removes.
    """
    state = _athlete(0.0, 0.0, 0.5)
    state.tissue_t.knee = 90.0
    for template in _every_template():
        spec = getattr(template, "scoring", None)
        if spec is None or spec.tissue_aggregate != "max" or "knee" not in spec.tissue_axes:
            continue
        candidate = score_template(template, state, {})
        assert candidate.tissue_penalty == pytest.approx(0.9 * spec.tissue_weight, rel=1e-9), (
            template.branch_id
        )
