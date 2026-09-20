"""The phase-1 simulation matrix runs, and its plausibility checks actually fire.

A report generator that silently produced an empty grid, or whose checks never triggered,
would be worse than no matrix: it would look like evidence of health. These tests pin the
shape of the run and prove the cross-cell checks can detect an implausible cell.
"""
from app.scripts import simulate_matrix as sm


def test_the_matrix_covers_the_declared_grid() -> None:
    cells = sm.build_matrix()

    assert len(cells) == len(sm.EXPERIENCE) * len(sm.FRESHNESS) * len(sm.GOALS) * len(sm.WORKLOADS)
    assert all(c.session for c in cells), "every cell must name the session it prescribed"
    assert all(c.duration_min > 0 for c in cells)


def test_every_cell_carries_a_real_dose() -> None:
    """A zero dose would make the state deltas meaningless."""
    assert all(c.dose_total > 0.0 for c in sm.build_matrix())


def test_the_cross_cell_checks_detect_an_implausible_cell() -> None:
    """Proof the checks are load-bearing: a planted fatigued>fresh cell is reported."""
    cells = sm.build_matrix()
    by_key = {(c.experience, c.freshness, c.goal, c.workload): c for c in cells}
    fresh = by_key[("novice", "fresh", "strength", "medium")]
    tired = by_key[("novice", "fatigued", "strength", "medium")]
    tired.sets = fresh.sets + 5  # a fatigued athlete given MORE work

    findings = sm.cross_cell_flags(cells)

    assert any("fatigued > fresh WORK" in f for f in findings), findings


def test_the_report_names_its_findings_rather_than_only_counting_them() -> None:
    report = sm.render(sm.build_matrix())

    assert "## Findings" in report and "## Matrix" in report
    assert "easy > hard DOSE" in report, "the known v0 density inversion should be reported"
    assert "v1 (shadow)" in report, "and explained against the corrected engine"
