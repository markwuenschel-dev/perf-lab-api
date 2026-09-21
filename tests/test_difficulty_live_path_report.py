"""The 3.4 evidence generator measures the live rule, and its conclusions cannot drift.

Phase 3.2's report compared candidates against a bare baseline and so missed half of what
`easy|medium|hard` does in production — the envelope's RPE shift, from which load is resolved.
That mistake is what these tests exist to prevent recurring: they assert the generator models
BOTH halves of the live rule, and they pin the two findings the 3.4 decision rests on.

If a future change makes one of these fail, the decision record in `phase-3-4.md` is stale and
must be re-derived, not patched.
"""
import pytest

from app.logic.planning import INTENSITY_RPE_CEILING
from app.scripts import compare_difficulty_live_path as clp

LEVELS = ("easy", "medium", "hard")


def _sets(structure) -> int:
    return sum(b.sets or 0 for b in structure)


def _rpe(structure) -> float:
    return structure[0].rpe_target


# ── the generator models the whole live rule ─────────────────────────────────


@pytest.mark.parametrize("family", sorted(clp.BASELINES))
def test_the_legacy_path_moves_both_sets_and_effort(family: str) -> None:
    """The defect that invalidated the 3.2 comparison: modelling sets only."""
    easy, hard = clp.legacy_path(family, "easy"), clp.legacy_path(family, "hard")

    assert _sets(easy) < _sets(hard), "the legacy rule moves working sets"
    assert _rpe(easy) < _rpe(hard), "and it moves the effort target, which resolves the load"


@pytest.mark.parametrize("family", sorted(clp.BASELINES))
def test_the_candidate_path_replaces_the_envelope_shift_rather_than_stacking_on_it(
    family: str,
) -> None:
    """Otherwise the comparison is `legacy + candidate` against `legacy`, which flatters it.

    The check: the candidate's own medium anchor is the UNSHIFTED envelope cap, so its hard
    effort is one full RIR from there — never the shifted cap plus another delta.
    """
    medium_cap = clp._envelope_cap("medium")

    assert _rpe(clp.candidate_path(family, "medium")) == medium_cap
    assert _rpe(clp.candidate_path(family, "hard")) == min(
        medium_cap + 1.0, INTENSITY_RPE_CEILING
    )


@pytest.mark.parametrize("family", sorted(clp.BASELINES))
@pytest.mark.parametrize("path", [clp.legacy_path, clp.candidate_path])
@pytest.mark.parametrize("level", LEVELS)
def test_no_path_prescribes_past_the_preference_ceiling(family, path, level) -> None:
    assert _rpe(path(family, level)) <= INTENSITY_RPE_CEILING


@pytest.mark.parametrize("family", sorted(clp.BASELINES))
def test_medium_is_the_same_session_on_both_paths(family: str) -> None:
    """The shared fixed point. Without it the two paths are not comparable at all."""
    assert clp.legacy_path(family, "medium") == clp.candidate_path(family, "medium")


# ── the findings the decision rests on ───────────────────────────────────────


@pytest.mark.parametrize("family", ["strength", "hypertrophy"])
def test_the_volume_families_reduce_to_half_a_point_of_rpe(family: str) -> None:
    """Why they stayed dormant: at representative set counts the volume half is a no-op.

    `1.2x5 = 6` and `1.25x4 = 5` land on exactly what the legacy `+1 set` already gives.
    """
    legacy, candidate = clp.legacy_path(family, "hard"), clp.candidate_path(family, "hard")

    assert _sets(legacy) == _sets(candidate), "the volume modifier adds nothing here"
    assert _rpe(candidate) - _rpe(legacy) == 0.5


def test_max_strength_trades_volume_for_load_and_v1_cannot_price_the_trade() -> None:
    """The finding that decided 3.4 — see C1 in docs/calibration-backlog.md.

    Not asserted as a magnitude threshold: the claim is the ORDERING failure, that the
    candidate's hard session is closer to medium than legacy's is, despite a heavier bar.
    """
    from app.logic import dose_engine_v1 as v1

    legacy = clp.legacy_path("max_strength", "hard")
    candidate = clp.candidate_path("max_strength", "hard")
    medium = clp.legacy_path("max_strength", "medium")

    assert _sets(candidate) < _sets(legacy), "fewer working sets"
    assert _rpe(candidate) > _rpe(legacy), "at a heavier bar"

    d_medium = clp._dose_total(v1, medium)
    assert clp._dose_total(v1, candidate) - d_medium < clp._dose_total(v1, legacy) - d_medium


# ── the report itself ────────────────────────────────────────────────────────


def test_the_report_states_the_decision_and_names_what_it_promoted() -> None:
    report = clp.render()

    assert "NOTHING PROMOTED" in report
    assert "Phase 3.4 decision" in report
    assert report.count("dormant") >= 3, "one per family"
    assert "promotion:\n    none" in report
    assert "supersedes `phase-3-2.md`" in report.lower()
