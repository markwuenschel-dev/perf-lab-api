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
