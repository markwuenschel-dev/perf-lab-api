"""Dose-law external load vs internal effort (ADR-0039).

External intensity is estimated from reps + reps-in-reserve (Epley %1RM) and is
independent of effort: a heavy triple and a high-rep set to failure differ in load.
"""

from app.logic.dose_engine_v0 import _external_intensity_from_reps


def test_heavy_low_rep_is_higher_load_than_high_rep():
    assert _external_intensity_from_reps(3, 1) > _external_intensity_from_reps(15, 0)


def test_load_depends_on_reps_to_failure_not_effort_split():
    # 5 reps with 2 in reserve and 7 reps to failure are the same external load.
    assert abs(_external_intensity_from_reps(5, 2) - _external_intensity_from_reps(7, 0)) < 1e-9


def test_unknown_reps_degrades_to_effort_only():
    assert _external_intensity_from_reps(None, None) == 1.0


def test_external_intensity_bounded():
    assert 0.3 <= _external_intensity_from_reps(40, 6) <= 1.0
    assert _external_intensity_from_reps(1, 0) <= 1.0
