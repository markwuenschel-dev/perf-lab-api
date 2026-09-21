"""Invariants the dose law must satisfy for every input it accepts (phase 0 harness).

These are UNIVERSAL claims — "no input produces a NaN", "more rest never means more
density" — so they are generated across the accepted input space rather than asserted on a
handful of hand-picked logs. The example-based suites (tests/test_dose_engine_v0.py,
tests/test_dose_parameters.py) stay: they pin specific numbers, these pin the shape of the law.

Two of these fail on today's engine and are marked xfail with the phase that fixes them, so
the suite stays honest without going red:

* **Density is two opposite quantities.** The session path computes
  ``duration / max(20, sets*5)`` — MINUTES PER SET (app/logic/dose_engine_v0.py:497-501), so a
  longer session at the same work scores as *denser*. The per-exercise path computes
  ``sets / rest_minutes`` (:598) — the reciprocal. Both are raised to the same ``dose_beta``.
* **Unguarded domain errors.** ``duration_minutes`` and ``total_volume_load`` carry no ``ge=``
  (app/schemas/workouts.py:105,110) and ``ExternalIntensity.value`` is unconstrained (:217), so
  ``log1p(V)`` and ``value ** dose_alpha`` can raise or return a complex number. The
  per-exercise twin floors its input (``max(0.1, vol_proxy)``, :606); the session path does not.

Physiological assumption under test: density is WORK PER UNIT TIME. Two sessions of equal work
differ in density only through elapsed time, and resting longer between efforts lowers it. No
claim is made here about how density should be weighted — only about its direction.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import ExternalIntensity, WorkoutLog

settings.register_profile("dose_ci", deadline=None, max_examples=200, derandomize=True)
settings.load_profile("dose_ci")

MODALITIES = ["Running", "Strength", "Hypertrophy", "Power", "Mixed"]

#: Every axis the engine publishes — the six-vector plus the legacy scalars clients still read.
LEGACY_AXES = (
    "d_met_systemic",
    "d_nm_peripheral",
    "d_nm_central",
    "d_struct_damage",
    "d_struct_signal",
)


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


def _axes(dose) -> dict[str, float]:
    six = dose.dose_six.model_dump()
    legacy = {name: getattr(dose, name) for name in LEGACY_AXES}
    return {**six, **legacy}


logs = st.builds(
    _log,
    modality=st.sampled_from(MODALITIES),
    duration_minutes=st.floats(min_value=0.0, max_value=300.0, allow_nan=False),
    session_rpe=st.floats(min_value=1.0, max_value=10.0, allow_nan=False),
    total_volume_load=st.floats(min_value=0.0, max_value=50_000.0, allow_nan=False),
    estimated_sets=st.one_of(st.none(), st.floats(min_value=1.0, max_value=60.0)),
    novelty=st.floats(min_value=0.1, max_value=3.0, allow_nan=False),
    avg_rir=st.one_of(st.none(), st.floats(min_value=0.0, max_value=10.0)),
)


# ── finiteness and sign ───────────────────────────────────────────────────────

@given(log=logs)
def test_every_dose_axis_is_finite_and_non_negative(log: WorkoutLog) -> None:
    """The headline invariant: no accepted input produces NaN, inf or a negative dose."""
    axes = _axes(calculate_stress_dose(log))

    for name, value in axes.items():
        assert math.isfinite(value), f"{name} is not finite: {value!r}"
        assert value >= 0.0, f"{name} is negative: {value!r}"


@given(log=logs)
def test_adaptation_contribution_is_finite_and_non_negative(log: WorkoutLog) -> None:
    contribution = calculate_stress_dose(log).adaptation_contribution.model_dump()

    for name, value in contribution.items():
        assert math.isfinite(value), f"{name} is not finite: {value!r}"
        assert value >= 0.0, f"{name} is negative: {value!r}"


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("zero duration", {"duration_minutes": 0.0}),
        ("zero volume load", {"total_volume_load": 0.0}),
        ("minimum sets", {"estimated_sets": 1.0}),
        ("zero duration and volume", {"duration_minutes": 0.0, "total_volume_load": 0.0}),
        ("trivial session", {"duration_minutes": 0.1, "estimated_sets": 1.0, "session_rpe": 1.0}),
    ],
)
def test_degenerate_sessions_do_not_produce_nan_or_inf(label: str, kwargs: dict) -> None:
    """Zero sets / rest / duration is a boundary, not an error — and never NaN."""
    axes = _axes(calculate_stress_dose(_log(**kwargs)))

    assert all(math.isfinite(v) for v in axes.values()), f"{label}: {axes}"
    assert all(v >= 0.0 for v in axes.values()), f"{label}: {axes}"


@pytest.mark.parametrize("field", ["duration_minutes", "total_volume_load"])
def test_negative_session_inputs_are_refused_at_the_boundary(field: str) -> None:
    """Nonsense must be refused where it arrives, not deep in the engine.

    This is about WHERE the failure happens. Before the guard a negative duration was
    accepted by the schema and raised ``math domain error`` inside ``log1p(V)`` — a 500 on a
    request that should have been a 422.
    """
    with pytest.raises(PydanticValidationError):
        _log(**{field: -50.0})


def test_negative_external_intensity_is_refused_at_the_boundary() -> None:
    """A negative load-relative-to-capacity is not a quiet dose — it is not a dose at all.

    Before the guard, ``value=-2.0`` made every axis complex (e.g. ``(-0.80-0.58j)``), which
    pydantic rejected when constructing the six-vector. The engine should never be asked to
    raise a negative base to a fractional exponent.
    """
    with pytest.raises(PydanticValidationError):
        ExternalIntensity(value=-2.0)


# ── density direction ─────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="C2b (docs/calibration-backlog.md): v0 reads density as MINUTES PER SET, and v1 — "
    "whose density INPUT is correct — still scales the density AXIS by a base that grows with "
    "duration, so this does not clear at 8C activation",
    strict=True,
)
@given(
    sets=st.floats(min_value=3.0, max_value=30.0),
    short=st.floats(min_value=20.0, max_value=60.0),
    stretch=st.floats(min_value=1.5, max_value=3.0),
)
def test_same_work_in_less_elapsed_time_is_denser(
    sets: float, short: float, stretch: float
) -> None:
    """Identical work, less elapsed time ⇒ strictly greater density."""
    assume(short * stretch <= 300.0)
    fast = calculate_stress_dose(_log(duration_minutes=short, estimated_sets=sets))
    slow = calculate_stress_dose(_log(duration_minutes=short * stretch, estimated_sets=sets))

    assert fast.dose_six.density > slow.dose_six.density


@pytest.mark.xfail(
    reason="C2b (docs/calibration-backlog.md): adding rest is stretching the session, and the "
    "density axis grows with duration under v0 and v1 alike — does not clear at 8C activation",
    strict=True,
)
@given(
    sets=st.floats(min_value=3.0, max_value=20.0),
    duration=st.floats(min_value=30.0, max_value=120.0),
    extra_rest=st.floats(min_value=5.0, max_value=60.0),
)
def test_more_rest_never_increases_density(
    sets: float, duration: float, extra_rest: float
) -> None:
    """More rest for the same work must not read as denser.

    Expressed as elapsed time because the session-level law has no rest input: adding rest
    to a fixed amount of work is exactly stretching the session.
    """
    assume(duration + extra_rest <= 300.0)
    tight = calculate_stress_dose(_log(duration_minutes=duration, estimated_sets=sets))
    rested = calculate_stress_dose(
        _log(duration_minutes=duration + extra_rest, estimated_sets=sets)
    )

    assert rested.dose_six.density <= tight.dose_six.density


# ── determinism ───────────────────────────────────────────────────────────────

@given(log=logs)
def test_dose_is_deterministic(log: WorkoutLog) -> None:
    """Same log, same dose — no RNG, no clock, no iteration-order dependence."""
    first = calculate_stress_dose(log).model_dump()
    second = calculate_stress_dose(log).model_dump()

    assert first == second


@given(log=logs)
def test_dose_does_not_mutate_its_input(log: WorkoutLog) -> None:
    before = log.model_dump()
    calculate_stress_dose(log)

    assert log.model_dump() == before
