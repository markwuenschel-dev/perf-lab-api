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

@pytest.mark.xfail(
    reason="phase 1.5 scoring ranges: _score_from_spec divides the tissue-axis SUM by 100 "
    "rather than 100*len(axes) (candidate_library.py:925), so a 3-axis template's penalty "
    "reaches 1.8; the weightlifting scorer adds two /100 terms (:1026) reaching 2.0",
    strict=True,
)
def test_a_summed_tissue_penalty_cannot_exceed_a_single_axis_penalty() -> None:
    """Listing more tissues must not multiply the penalty.

    Worst case per template, computed from the declared scoring spec: an athlete with every
    tissue axis at 100 must not produce a penalty above the declared 0–1 ceiling.
    """
    offenders: list[str] = []
    for template in _every_template():
        spec = getattr(template, "scoring", None)
        if spec is None or not getattr(spec, "tissue_axes", ()):
            continue
        worst = len(spec.tissue_axes) * 100.0 / 100.0 * spec.tissue_weight
        if worst > 1.0:
            offenders.append(
                f"{template.branch_id}: {len(spec.tissue_axes)} axes × weight "
                f"{spec.tissue_weight} → {worst:.2f}"
            )

    assert not offenders, "tissue penalties above the declared 0–1 range:\n" + "\n".join(offenders)


@pytest.mark.parametrize("axis", NORMALIZED_AXES)
def test_the_weighted_score_stays_in_zero_one(axis: str) -> None:
    """Whatever an axis carries, the final score is clamped (holds today — keep it)."""
    score = score_candidate(_candidate(**{axis: 5.0}))

    assert 0.0 <= score <= 1.0


# ── omission invariance ───────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="phase 1.5 neutral defaults: goal_alignment and state_fit default to 1.0 "
    "(candidate.py:32-33), so a candidate that sets neither collects 0.55 of score for free",
    strict=True,
)
def test_omitting_an_axis_is_neutral_rather_than_maximal() -> None:
    """A candidate making no claim must not outscore one making a modest claim."""
    silent = _candidate()
    modest = _candidate(goal_alignment=0.5, state_fit=0.5)

    assert score_candidate(silent) <= score_candidate(modest), (
        f"silent={score_candidate(silent):.3f} beat modest={score_candidate(modest):.3f}"
    )


@pytest.mark.xfail(
    reason="phase 1.5 neutral defaults: same root cause — the unset default is the best "
    "possible value, so omission changes the score",
    strict=True,
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
