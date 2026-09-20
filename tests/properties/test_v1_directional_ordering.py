"""v1's ORDERING must be physiologically sensible, even though its magnitudes are not fitted.

The pre-calibration bar, stated explicitly: for two sessions differing only in the variable
under test, v1 must rank them the way the physiology says. Absolute dose is not asserted
anywhere here — that is calibration (phase 8), and asserting it now would bake the untuned
coefficients into tests.

Why ordering specifically: a scale difference can be calibrated away, but a RANK INVERSION
cannot. If two sessions formerly ranked A > B come out B > A, re-fitting a coefficient will
not fix it — the predictor is saying something different about the athlete. So these tests
guard the property that survives calibration.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.logic import dose_engine_v1 as v1
from app.schemas.workouts import WorkoutLog


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.5,
        "estimated_sets": 20.0,
        "total_volume_load": 8000.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _density(log: WorkoutLog) -> float:
    """The shaped density AXIS — density times the session's base dose."""
    return v1.calculate_stress_dose(log).dose_six.density


def _density_variable(log: WorkoutLog) -> float:
    """The density VARIABLE itself, before the dose law scales it by the session's base."""
    from app.engine.parameters import default_parameters

    return v1.session_density(log, float(log.estimated_sets or 0.0), default_parameters()).factor


def _total(log: WorkoutLog) -> float:
    return sum(v1.calculate_stress_dose(log).dose_six.model_dump().values())


# ── the same work, compressed ─────────────────────────────────────────────────

def test_the_same_sets_in_half_the_time_carry_more_density() -> None:
    """20 sets in 30 minutes is denser work than 20 sets in 60."""
    assert _density(_log(duration_minutes=30.0)) > _density(_log(duration_minutes=60.0))


@pytest.mark.parametrize(("quick", "slow"), [(30.0, 45.0), (45.0, 60.0), (60.0, 90.0)])
def test_compression_ordering_holds_across_the_duration_range(quick: float, slow: float) -> None:
    assert _density(_log(duration_minutes=quick)) >= _density(_log(duration_minutes=slow))


# ── more work in the same time ────────────────────────────────────────────────

def test_more_sets_in_the_same_time_carry_more_dose() -> None:
    """20 sets in an hour is more work than 10 sets in an hour."""
    assert _total(_log(estimated_sets=20.0)) > _total(_log(estimated_sets=10.0))


@pytest.mark.parametrize(("more", "fewer"), [(30.0, 20.0), (20.0, 12.0), (12.0, 6.0)])
def test_set_count_ordering_holds_across_the_range(more: float, fewer: float) -> None:
    assert _total(_log(estimated_sets=more)) >= _total(_log(estimated_sets=fewer))


# ── endurance must not inherit a fabricated set count ─────────────────────────

@pytest.mark.parametrize("fabricated_sets", [3.0, 5.0, 12.0, 40.0])
def test_an_endurance_sessions_density_ignores_whatever_set_count_is_attached(
    fabricated_sets: float,
) -> None:
    """The density half of the claim, which v1 satisfies."""
    from app.engine.parameters import default_parameters

    p = default_parameters()
    bare = _log(modality="Running", estimated_sets=None)
    attached = _log(modality="Running", estimated_sets=fabricated_sets)

    assert v1.session_density(bare, 5.0, p).value is None
    assert v1.session_density(attached, fabricated_sets, p).value is None


# Every value, including 5.0 — which equals max(3, 60/12) and so used to pass only by
# coinciding with the fallback. Since phase 1.2b no set count reaches an endurance dose at all.
@pytest.mark.parametrize("fabricated_sets", [3.0, 5.0, 12.0, 40.0])
def test_an_endurance_session_ignores_whatever_set_count_is_attached(
    fabricated_sets: float,
) -> None:
    """Changing the fallback set logic must not move an endurance dose AT ALL.

    v0 derives ``sets = max(3, duration/12)`` for a continuous run and feeds it into both
    density and the volume proxy. v1 stopped using it for density in phase 1.2 and for volume
    in phase 1.2b, so tuning that fallback can no longer re-weight any runner's history.
    """
    baseline = _total(_log(modality="Running", estimated_sets=None, total_volume_load=0.0))
    with_sets = _total(
        _log(modality="Running", estimated_sets=fabricated_sets, total_volume_load=0.0)
    )

    assert with_sets == pytest.approx(baseline, rel=1e-9), (
        "an endurance dose moved because of a fabricated set count"
    )


def test_endurance_density_is_declared_not_modelled_rather_than_averaged() -> None:
    dose = v1.calculate_stress_dose(_log(modality="Running", estimated_sets=None))

    assert dose.density_basis == "not_applicable"
    assert dose.dose_model_version == "v1"


# ── orderings that must survive the re-fit ────────────────────────────────────

def test_a_harder_session_outranks_an_easier_one_at_equal_everything_else() -> None:
    assert _total(_log(session_rpe=9.0)) > _total(_log(session_rpe=5.0))


def test_a_longer_session_at_the_same_pace_outranks_a_shorter_one() -> None:
    """Same density (sets scale with duration), more total work."""
    short = _log(duration_minutes=30.0, estimated_sets=10.0, total_volume_load=4000.0)
    long = _log(duration_minutes=60.0, estimated_sets=20.0, total_volume_load=8000.0)

    # Same pace: the density VARIABLE matches. (The shaped density AXIS does not, because the
    # law multiplies it by a base dose that grows with the session — which is the intent.)
    assert _density_variable(long) == pytest.approx(_density_variable(short), rel=1e-6)
    assert _total(long) > _total(short)


def test_model_version_travels_with_every_dose() -> None:
    """Whatever else changes, a stored dose can always name the model that produced it."""
    for modality in ("Strength", "Hypertrophy", "Power", "Running", "Mixed"):
        dose = v1.calculate_stress_dose(_log(modality=modality))
        assert dose.dose_model_version == "v1", modality
        assert dose.density_basis in {"sets_per_elapsed_minute", "not_applicable"}, modality


# ── the volume proxy's set count (phase 1.2b) ─────────────────────────────────

@pytest.mark.parametrize(
    ("modality", "sets", "expected_basis"),
    [
        ("Strength", 20.0, "reported"),
        ("Strength", None, "unreported"),
        ("Running", None, "not_counted"),
        ("Running", 12.0, "not_counted"),
        ("Mixed", 10.0, "not_counted"),
    ],
)
def test_every_v1_dose_says_where_its_volume_set_count_came_from(
    modality: str, sets: float | None, expected_basis: str
) -> None:
    """The shadow dataset must be able to tell measured sets from absent ones."""
    dose = v1.calculate_stress_dose(_log(modality=modality, estimated_sets=sets))

    assert dose.volume_sets_basis == expected_basis


def test_v0_still_uses_and_labels_its_fabricated_set_count() -> None:
    """v0 is frozen, not fixed — and its fallback is now LABELLED, so 8B can exclude it."""
    from app.logic import dose_engine_v0 as v0

    fabricated = v0.calculate_stress_dose(_log(modality="Running", estimated_sets=None))
    reported = v0.calculate_stress_dose(_log(modality="Strength", estimated_sets=20.0))

    assert fabricated.volume_sets_basis == "fabricated_fallback"
    assert reported.volume_sets_basis == "reported"


def test_reported_strength_sets_still_drive_v1_volume() -> None:
    """The fix removes invented sets, not real ones."""
    assert _total(_log(estimated_sets=30.0)) > _total(_log(estimated_sets=10.0))


# ── ACTIVATION GATE: workload monotonicity (phase 8C) ────────────────────────
#
# A permanent criterion for activating v1 in production, added after the phase-1 simulation
# matrix found the live model inverted: for otherwise-identical generated STRENGTH sessions
# where only the set count changes,
#
#     easy sets < medium sets < hard sets   =>   dose(easy) < dose(medium) < dose(hard)
#
# and projected adaptation must not reverse solely because more work was packed into the same
# session duration. v0 fails this (2.89 / 2.01 / 1.55 — see docs/simulations/phase-1.md); v1
# must pass it before it can take production authority.
#
# Deliberately NOT a universal law: once difficulty is multidimensional, a low-volume maximal
# session can legitimately out-dose a high-volume hypertrophy one. The claim here is narrow —
# ONE controlled transformation, set count, everything else held.

_STRENGTH_WEEK = {
    "block_goal": "Strength",
    "session_category": "Max Strength",
    "session_domain": "strength",
    "week_number": 2,
    "duration_weeks": 8,
    "deload_every_n_weeks": 4,
}


def _prescribed(workload: str):
    """The session the engine generates for this workload, and its dose under v1."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_prescriber_candidates import _state

    from app.logic.prescriber import recommend_next_session

    rx = recommend_next_session(
        _state(max_strength=50.0), goal="Strength",
        block_context=dict(_STRENGTH_WEEK, intensity=workload),
    )
    sets = sum(e.sets or 0 for e in rx.exercises)
    dose = v1.calculate_stress_dose(
        _log(
            modality="Strength",
            duration_minutes=max(1.0, float(rx.duration_min)),
            estimated_sets=float(sets) if sets else None,
        )
    )
    return sets, float(sum(dose.dose_six.model_dump().values()))


def test_workload_sets_increase_easy_to_hard() -> None:
    """Precondition for the gate: the preference really does change the prescribed work."""
    easy, medium, hard = (_prescribed(w)[0] for w in ("easy", "medium", "hard"))

    assert easy < medium < hard, f"sets were {easy}/{medium}/{hard}"


def test_more_prescribed_work_in_the_same_session_carries_more_dose() -> None:
    """THE ACTIVATION GATE. v0 inverts this; v1 must not.

    If this ever fails for v1, v1 must not be activated (phase 8C) — an athlete choosing a
    harder week would train more while the model recorded less.
    """
    (_e_sets, easy), (_m_sets, medium), (_h_sets, hard) = (
        _prescribed(w) for w in ("easy", "medium", "hard")
    )

    assert easy < medium < hard, (
        f"v1 dose is not monotonic in prescribed work: {easy:.2f} / {medium:.2f} / {hard:.2f}"
    )


def test_the_inversion_this_gate_exists_for_is_real_in_production() -> None:
    """Pins the defect the gate guards against, so the gate cannot be mistaken for theory.

    Same fixed session, only the set count differing, under the PRODUCTION engine.
    """
    from app.logic import dose_engine_v0 as v0

    def total(engine, sets: int) -> float:
        dose = engine.calculate_stress_dose(
            _log(modality="Strength", duration_minutes=75.0, estimated_sets=float(sets))
        )
        return float(sum(dose.dose_six.model_dump().values()))

    assert total(v0, 8) > total(v0, 14), "v0 no longer inverts — re-read this gate's premise"
    assert total(v1, 8) < total(v1, 14), "v1 must order more work as more dose"
