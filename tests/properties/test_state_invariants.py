"""Invariants the state update must satisfy for every elapsed time (phase 0 harness).

``update_athlete_state`` (app/logic/state_update_v0.py:534) runs seven steps in a fixed order.
Two of them are in the wrong order relative to each other, and these tests say so:

* **Detraining runs AFTER adaptation.** Step 5 applies this session's adaptation gains (:603);
  step 5b then erodes every capacity by ``rate × elapsed_days`` (:606-611). So a session logged
  after a fortnight off has its brand-new gain decayed by fourteen days of not training — days
  that happened BEFORE the session. Elapsed-time decay belongs to the state the session
  started from.
* The fatigue and tissue halves already get this right: decay at :550-560, impulses at :563-571.

Physiological assumptions under test:
* Detraining is a function of time NOT training. Today's session is not part of that window.
* Zero elapsed time means zero decay — of fatigue, tissue and capacity alike.
* The update is a pure function of (state, dose, elapsed, log). No clock, no RNG.

The example-based suite (tests/test_state_update_unit.py, tests/test_state_update_v2.py) keeps
pinning specific magnitudes; this file pins the ordering and the boundaries.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog

settings.register_profile("state_ci", deadline=None, max_examples=150, derandomize=True)
settings.load_profile("state_ci")

_T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _state(
    *,
    cns: float = 5.0,
    muscular: float = 5.0,
    max_strength: float = 50.0,
    work_capacity: float = 50.0,
) -> UnifiedStateVector:
    x = CapacityState(
        max_strength=max_strength,
        aerobic=300.0,
        hypertrophy=50.0,
        work_capacity=work_capacity,
    )
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    legacy = sync_legacy_from_vectors(x, f, t)
    return UnifiedStateVector(
        timestamp=_T0,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **legacy,
    )


def _log(**kwargs) -> WorkoutLog:
    defaults = {
        "timestamp": _T0,
        "modality": "Strength",
        "duration_minutes": 60.0,
        "session_rpe": 7.5,
        "total_volume_load": 6000.0,
        "estimated_sets": 18.0,
        "sleep_quality": 7.0,
        "life_stress_inverse": 7.0,
    }
    defaults.update(kwargs)
    return WorkoutLog(**defaults)


def _dose(**kwargs) -> StressDose:
    return calculate_stress_dose(_log(**kwargs))


def _capacities(state: UnifiedStateVector) -> dict[str, float]:
    return {key: getattr(state.capacity_x, key) for key in state.capacity_x.KEYS}


# ── no elapsed time, no decay ─────────────────────────────────────────────────

def test_zero_elapsed_time_does_not_detrain_any_capacity() -> None:
    """Holds today (state_update_v0.py:607 guards on days > 0) — pinned so it keeps holding."""
    before = _state()
    after = update_athlete_state(before, StressDose(), timedelta(0), _log(session_rpe=1.0))

    for key, value in _capacities(before).items():
        assert _capacities(after)[key] >= value - 1e-9, f"{key} eroded with zero elapsed time"


@given(minutes=st.floats(min_value=0.0, max_value=59.0))
def test_sub_hour_gaps_do_not_erode_capacity_measurably(minutes: float) -> None:
    before = _state()
    after = update_athlete_state(
        before, StressDose(), timedelta(minutes=minutes), _log(session_rpe=1.0)
    )

    for key, value in _capacities(before).items():
        # A fraction of a day of detraining is allowed, but it may never exceed a full day's.
        rate = default_parameters().capacity_decay_per_day.get(key, 0.001)
        assert _capacities(after)[key] >= value * (1.0 - rate) - 1e-9


# ── the ordering defect ───────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="phase 1.3 decay order: detraining (state_update_v0.py:606-611) runs AFTER the "
    "adaptation gains (:603), so a session's own new adaptation is decayed by the idle days "
    "that preceded it",
    strict=True,
)
@pytest.mark.parametrize("idle_days", [7, 14, 30])
def test_a_session_after_idle_days_does_not_decay_its_own_adaptation(idle_days: int) -> None:
    """Return-from-layoff: the gain earned TODAY must not be eroded by days already past.

    Two athletes with identical state log the identical session. One trained yesterday, one
    is back after ``idle_days`` off. The returner's capacities should be LOWER by the
    detraining of the gap — but the gain from today's session, measured against each
    athlete's own decayed starting point, must be the same.
    """
    dose = _dose()
    fresh_before = _state()
    idle_before = _state()

    fresh_after = update_athlete_state(fresh_before, dose, timedelta(days=1), _log())
    idle_after = update_athlete_state(idle_before, dose, timedelta(days=idle_days), _log())

    # What the idle athlete's capacity would be with the gap's decay but no session.
    decayed_only = update_athlete_state(
        _state(), StressDose(), timedelta(days=idle_days), _log(session_rpe=1.0)
    )
    fresh_decayed_only = update_athlete_state(
        _state(), StressDose(), timedelta(days=1), _log(session_rpe=1.0)
    )

    for key in fresh_before.capacity_x.KEYS:
        fresh_gain = getattr(fresh_after.capacity_x, key) - getattr(
            fresh_decayed_only.capacity_x, key
        )
        idle_gain = getattr(idle_after.capacity_x, key) - getattr(decayed_only.capacity_x, key)
        if fresh_gain <= 1e-9:
            continue  # this axis gained nothing from the session; nothing to protect
        assert idle_gain == pytest.approx(fresh_gain, rel=1e-6), (
            f"{key}: the returning athlete's own session gain was decayed by the gap "
            f"({idle_gain:.6f} vs {fresh_gain:.6f})"
        )


@pytest.mark.xfail(
    reason="phase 1.3 decay order: same root cause — the longer the layoff, the more of "
    "today's adaptation is eaten by it",
    strict=True,
)
def test_longer_layoffs_do_not_shrink_todays_gain() -> None:
    """The gain from an identical session must not depend on how long the athlete was away."""
    dose = _dose()
    gains = []
    for days in (1, 14, 60):
        after = update_athlete_state(_state(), dose, timedelta(days=days), _log())
        baseline = update_athlete_state(
            _state(), StressDose(), timedelta(days=days), _log(session_rpe=1.0)
        )
        gains.append(after.capacity_x.max_strength - baseline.capacity_x.max_strength)

    assert gains[0] == pytest.approx(gains[1], rel=1e-6)
    assert gains[0] == pytest.approx(gains[2], rel=1e-6)


# ── decay precedes adaptation ─────────────────────────────────────────────────

def test_fatigue_decays_before_this_sessions_impulse_lands() -> None:
    """Order check on the half that is already correct (decay :550, impulse :563).

    Starting fatigue must be decayed by the gap, then this session's impulse added on top —
    so a long gap leaves LESS total fatigue than a short one for the same dose.
    """
    dose = _dose(session_rpe=9.0)
    short_gap = update_athlete_state(_state(cns=60.0), dose, timedelta(hours=24), _log())
    long_gap = update_athlete_state(_state(cns=60.0), dose, timedelta(days=10), _log())

    assert long_gap.fatigue_f.cns < short_gap.fatigue_f.cns


# ── determinism and purity ────────────────────────────────────────────────────

@given(
    hours=st.floats(min_value=0.0, max_value=24.0 * 90),
    rpe=st.floats(min_value=1.0, max_value=10.0),
)
def test_state_update_is_deterministic(hours: float, rpe: float) -> None:
    dose = _dose(session_rpe=rpe)
    first = update_athlete_state(_state(), dose, timedelta(hours=hours), _log(session_rpe=rpe))
    second = update_athlete_state(_state(), dose, timedelta(hours=hours), _log(session_rpe=rpe))

    assert first.model_dump() == second.model_dump()


@given(hours=st.floats(min_value=0.0, max_value=24.0 * 30))
def test_state_update_does_not_mutate_the_previous_state(hours: float) -> None:
    """Append-only: the previous state is evidence, not scratch space."""
    before = _state()
    snapshot = before.model_dump()
    update_athlete_state(before, _dose(), timedelta(hours=hours), _log())

    assert before.model_dump() == snapshot


@given(hours=st.floats(min_value=0.0, max_value=24.0 * 365))
def test_every_state_field_stays_finite_and_in_range(hours: float) -> None:
    after = update_athlete_state(_state(), _dose(), timedelta(hours=hours), _log())

    for key in after.fatigue_f.KEYS:
        value = getattr(after.fatigue_f, key)
        assert math.isfinite(value) and 0.0 <= value <= 100.0, f"fatigue.{key}={value}"
    for key in after.tissue_t.KEYS:
        value = getattr(after.tissue_t, key)
        assert math.isfinite(value) and 0.0 <= value <= 100.0, f"tissue.{key}={value}"
    for key, value in _capacities(after).items():
        assert math.isfinite(value) and value >= 0.0, f"capacity.{key}={value}"


def test_negative_elapsed_time_is_treated_as_zero_not_as_growth() -> None:
    """A clock skew must never run the model backwards (guarded at :540-542).

    Only the physiology is compared: ``timestamp`` legitimately differs, because it records
    when the session happened, not how much time the model integrated over.
    """
    forward = update_athlete_state(_state(), _dose(), timedelta(0), _log())
    backward = update_athlete_state(_state(), _dose(), timedelta(hours=-48), _log())

    physiology = {"timestamp"}
    assert {
        k: v for k, v in backward.model_dump().items() if k not in physiology
    } == {k: v for k, v in forward.model_dump().items() if k not in physiology}
