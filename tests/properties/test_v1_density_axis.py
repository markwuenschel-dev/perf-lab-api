"""C2b, fixed in v1.2: the density AXIS is temporal compression, not session length.

Before v1.2 every axis was ``base · m[axis] · …`` and ``base`` carries ``log1p(V)``, whose
volume proxy ``V`` includes elapsed minutes. So at fixed work a longer session raised ``base``,
and with it the density axis. That only won when Δ was saturated at the floor or cap — across
2018 grid pairs with both Δ strictly inside the band there were 0 inversions (phase 8.3) —
but a saturated Δ is exactly the fast session the density axis exists to reward.

v1.2 builds the density axis from a named ``work_volume_component``: the work the session
did, computed from work inputs only (reported sets and external load, or the timed work of a
timed-work density). Elapsed time reaches the density axis ONLY through Δ.

What is and is not claimed, precisely:

* The density axis is non-increasing in elapsed time at fixed work, strictly decreasing
  where Δ is inside its band, and flat where Δ is saturated at the same bound.
* The fix moves the density axis and nothing else: the other five axes and the adaptation
  contribution are bit-identical to the pre-fix (v1.1) formula. ``d_met_systemic`` moves,
  because it is a legacy channel computed FROM the density axis.
* The other five axes are NOT invariant to elapsed time at fixed work. They still carry
  ``log1p(V)`` (duration is a term of ``V``, C1) and ``Δ^β``. That is a separate dose-law
  question and is not addressed here.

v0 is frozen for replay and keeps C2b; its two xfails in test_dose_invariants.py stay strict.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.engine.parameters import default_parameters
from app.logic import dose_engine_v1
from app.logic.dose_engine_v0 import calculate_stress_dose as _shared_law
from app.logic.dose_engine_v1 import (
    WORK_PER_TIME_DENSITY,
    calculate_stress_dose,
    work_volume_component,
)
from app.logic.dose_model import DensityMeasurement
from app.schemas.workouts import StressDose, WorkoutLog

settings.register_profile("dose_ci", deadline=None, max_examples=200, derandomize=True)
settings.load_profile("dose_ci")

P = default_parameters()
FLOOR, CAP = P.dose_delta_floor, P.dose_delta_cap

#: v1 exactly as it was before the fix: the same variables with the density-axis hook removed.
PRE_FIX = replace(WORK_PER_TIME_DENSITY, density_axis_volume=None)

OTHER_AXES = ("volume", "intensity", "impact", "skill", "metabolic")


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _pre_fix(log: WorkoutLog) -> StressDose:
    return _shared_law(log, P, dose_variables=PRE_FIX)


def _unsaturated(value: float | None) -> bool:
    return value is not None and FLOOR < value < CAP


# ── the two phase-0 density invariants, against v1 ────────────────────────────

@given(
    sets=st.floats(min_value=3.0, max_value=30.0),
    short=st.floats(min_value=20.0, max_value=60.0),
    stretch=st.floats(min_value=1.5, max_value=3.0),
    load=st.floats(min_value=0.0, max_value=20000.0),
)
def test_v1_same_work_in_less_elapsed_time_is_denser(
    sets: float, short: float, stretch: float, load: float
) -> None:
    """Identical work, less elapsed time ⇒ never less dense; strictly denser unless Δ saturates.

    The strict half cannot hold where both sessions' Δ sit at the same bound: the clamp says
    "no further density credit", so their density axes are equal. That is the clamp, not C2b.
    """
    assume(short * stretch <= 300.0)
    fast = calculate_stress_dose(
        _log(duration_minutes=short, estimated_sets=sets, total_volume_load=load)
    )
    slow = calculate_stress_dose(
        _log(duration_minutes=short * stretch, estimated_sets=sets, total_volume_load=load)
    )

    assert fast.dose_six.density >= slow.dose_six.density
    if fast.density_value != slow.density_value:
        assert fast.dose_six.density > slow.dose_six.density


@given(
    sets=st.floats(min_value=3.0, max_value=20.0),
    duration=st.floats(min_value=30.0, max_value=120.0),
    extra_rest=st.floats(min_value=5.0, max_value=60.0),
)
def test_v1_more_rest_never_increases_density(
    sets: float, duration: float, extra_rest: float
) -> None:
    """More rest for the same work never reads as denser (the phase-0 invariant, verbatim)."""
    assume(duration + extra_rest <= 300.0)
    tight = calculate_stress_dose(_log(duration_minutes=duration, estimated_sets=sets))
    rested = calculate_stress_dose(
        _log(duration_minutes=duration + extra_rest, estimated_sets=sets)
    )

    assert rested.dose_six.density <= tight.dose_six.density


# ── unclamped and saturated, by example ──────────────────────────────────────

def test_unclamped_density_axis_falls_strictly_as_elapsed_time_grows() -> None:
    """12 sets in 30 / 40 / 60 / 90 minutes: Δ 2.0 → 0.67, all strictly inside the band."""
    doses = [
        calculate_stress_dose(_log(duration_minutes=d, estimated_sets=12.0))
        for d in (30.0, 40.0, 60.0, 90.0)
    ]
    assert all(_unsaturated(d.density_value) for d in doses)
    axes = [d.dose_six.density for d in doses]
    assert axes == sorted(axes, reverse=True)
    assert len(set(axes)) == len(axes)


def test_the_c2b_falsifying_example_no_longer_inverts() -> None:
    """calibration-backlog.md C2b: 20 sets in 20 vs 40 min, both Δ at the cap.

    v1.1 reported 1.436 for the faster session and 1.535 for the slower one. At fixed work
    and equal (saturated) Δ, elapsed time must not move the density axis at all.
    """
    fast = calculate_stress_dose(_log(duration_minutes=20.0, estimated_sets=20.0))
    slow = calculate_stress_dose(_log(duration_minutes=40.0, estimated_sets=20.0))
    assert fast.density_value == slow.density_value == CAP
    assert fast.dose_six.density == pytest.approx(slow.dose_six.density, rel=1e-12)

    before_fast, before_slow = _pre_fix(_log(duration_minutes=20.0, estimated_sets=20.0)), _pre_fix(
        _log(duration_minutes=40.0, estimated_sets=20.0)
    )
    assert before_fast.dose_six.density < before_slow.dose_six.density  # the defect, pinned


# ── the fix touches the density axis and nothing else ─────────────────────────

@given(
    modality=st.sampled_from(["Running", "Strength", "Hypertrophy", "Power", "Mixed"]),
    duration=st.floats(min_value=5.0, max_value=240.0),
    sets=st.one_of(st.none(), st.floats(min_value=1.0, max_value=40.0)),
    load=st.floats(min_value=0.0, max_value=20000.0),
    rpe=st.floats(min_value=1.0, max_value=10.0),
)
def test_only_the_density_axis_differs_from_the_pre_fix_formula(
    modality: str, duration: float, sets: float | None, load: float, rpe: float
) -> None:
    log = _log(
        modality=modality,
        duration_minutes=duration,
        estimated_sets=sets,
        total_volume_load=load,
        session_rpe=rpe,
    )
    fixed, before = calculate_stress_dose(log), _pre_fix(log)

    for axis in OTHER_AXES:
        assert getattr(fixed.dose_six, axis) == getattr(before.dose_six, axis), axis
    assert fixed.adaptation_contribution == before.adaptation_contribution
    for channel in ("d_nm_peripheral", "d_nm_central", "d_struct_damage", "d_struct_signal"):
        assert getattr(fixed, channel) == getattr(before, channel), channel
    assert fixed.density_value == before.density_value
    assert fixed.density_basis == before.density_basis


@given(
    sets=st.floats(min_value=3.0, max_value=30.0),
    d1=st.floats(min_value=5.0, max_value=240.0),
    d2=st.floats(min_value=5.0, max_value=240.0),
    load=st.floats(min_value=0.0, max_value=20000.0),
)
def test_elapsed_time_reaches_the_density_axis_only_through_delta(
    sets: float, d1: float, d2: float, load: float
) -> None:
    """At fixed work, density axis / Δ^(β+1) does not depend on elapsed time.

    Every other factor of the density-axis base is the same for both sessions (same work,
    RPE, novelty, wellness, external intensity), so after dividing out Δ what remains is a
    function of the work alone.
    """
    a = calculate_stress_dose(_log(duration_minutes=d1, estimated_sets=sets, total_volume_load=load))
    b = calculate_stress_dose(_log(duration_minutes=d2, estimated_sets=sets, total_volume_load=load))
    assert a.density_value is not None and b.density_value is not None
    k = P.dose_beta + 1.0
    assert a.dose_six.density / a.density_value**k == pytest.approx(
        b.dose_six.density / b.density_value**k, rel=1e-9
    )


# ── boundaries ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "log",
    [
        _log(modality="Running", duration_minutes=45.0, distance_meters=9000.0),
        _log(modality="Mixed", duration_minutes=50.0, estimated_sets=12.0),
        _log(modality="Strength", duration_minutes=60.0),  # sets not reported
    ],
    ids=["running", "mixed", "strength-unreported-sets"],
)
def test_density_not_modelled_is_unchanged(log: WorkoutLog) -> None:
    """No density measurement ⇒ no work component ⇒ the pre-fix density axis, bit-for-bit."""
    fixed = calculate_stress_dose(log)
    assert fixed.density_basis == "not_applicable"
    assert fixed.dose_six == _pre_fix(log).dose_six


@given(
    work_min=st.floats(min_value=5.0, max_value=60.0),
    rest_short=st.floats(min_value=0.0, max_value=30.0),
    rest_extra=st.floats(min_value=1.0, max_value=60.0),
)
def test_prescribed_timed_work_fixed_work_less_elapsed_is_denser(
    work_min: float, rest_short: float, rest_extra: float
) -> None:
    """The phase-5.4 shadow proxy is unclamped in (0, 1]: shorter elapsed ⇒ strictly denser."""
    def dose(elapsed: float) -> StressDose:
        measured = dose_engine_v1.prescribed_work_density(work_min * 60.0, elapsed)
        return calculate_stress_dose(
            _log(modality="Running", duration_minutes=elapsed), prescribed_density=measured
        )

    tight, loose = dose(work_min + rest_short), dose(work_min + rest_short + rest_extra)
    assert tight.density_basis == loose.density_basis == "prescribed_timed_work_over_elapsed"
    assert tight.dose_six.density > loose.dose_six.density


def test_work_volume_component_reads_work_inputs_only() -> None:
    """The named quantity never sees elapsed time except as the work of a timed-work density."""
    vw = P.dose_volume_weights
    sets_density = DensityMeasurement(value=1.2, basis="sets_per_elapsed_minute")
    for minutes in (20.0, 60.0, 180.0):
        log = _log(duration_minutes=minutes, estimated_sets=10.0, total_volume_load=4000.0)
        assert work_volume_component(log, 10.0, sets_density, P) == pytest.approx(
            vw["volume_load"] * 4000.0 + vw["sets"] * 10.0
        )

    timed = DensityMeasurement(value=0.5, basis="prescribed_timed_work_over_elapsed")
    run = _log(modality="Running", duration_minutes=40.0)
    assert work_volume_component(run, 0.0, timed, P) == pytest.approx(vw["duration"] * 20.0)

    assert work_volume_component(run, 0.0, DensityMeasurement(None, "not_applicable"), P) is None


def test_v1_is_recorded_as_v1_2() -> None:
    assert WORK_PER_TIME_DENSITY.version == "v1.2"
    assert calculate_stress_dose(_log(estimated_sets=10.0)).dose_model_version == "v1.2"
