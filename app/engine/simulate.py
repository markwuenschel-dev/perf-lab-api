"""
Deterministic, DB-free simulation harness for the state engine.

Drives synthetic workout (and benchmark) sequences through the *real* dose and
state-update pipeline so engine behavior can be asserted as **trajectories**
rather than single steps. This is the test bed the math decisions tune against —
see docs/adr/0032-relative-state-math-benchmark-anchored.md and ADR-0033/0034/
0037/0039.

Everything here is pure Python (no DB, no async), so scenarios run fast and in
CI. Construction mirrors the patterns in tests/test_state_update_v2.py and reuses
``sync_legacy_from_vectors`` so the legacy scalar mirrors stay consistent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.domain.vectors import CapacityState, FatigueState, TissueState
from app.engine.state_bridge import build_unified_state_vector
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.state_update_v0 import apply_benchmark_observation, update_athlete_state
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

LogFactory = Callable[[datetime], WorkoutLog]


# ---------------------------------------------------------------------------
# State / log construction
# ---------------------------------------------------------------------------

def baseline_state(
    *,
    when: datetime | None = None,
    habit_strength: float = 0.5,
    **capacity_overrides: float,
) -> UnifiedStateVector:
    """A fresh (zero-fatigue) athlete state with optional capacity overrides.

    Example: ``baseline_state(max_strength=70.0, aerobic=320.0)``.
    """
    cx = CapacityState(**capacity_overrides)
    f = FatigueState()
    t = TissueState()
    return build_unified_state_vector(
        timestamp=when or _EPOCH,
        x=cx,
        f=f,
        t=t,
        s_struct_signal=0.0,
        habit_strength=habit_strength,
        skill_state={},
    )


def make_log(
    when: datetime,
    modality: Literal["Running", "Strength", "Hypertrophy", "Power", "Mixed"] = "Strength",
    *,
    duration_minutes: float = 60.0,
    session_rpe: float = 7.0,
    avg_rir: float | None = None,
    sleep_quality: float = 7.0,
    life_stress_inverse: float = 7.0,
    distance_meters: float | None = None,
    total_volume_load: float | None = None,
    dominant_movement_pattern: str | None = None,
    novelty: float = 1.0,
    estimated_sets: float | None = None,
) -> WorkoutLog:
    return WorkoutLog(
        timestamp=when,
        modality=modality,
        duration_minutes=duration_minutes,
        session_rpe=session_rpe,
        avg_rir=avg_rir,
        sleep_quality=sleep_quality,
        life_stress_inverse=life_stress_inverse,
        distance_meters=distance_meters,
        total_volume_load=total_volume_load,
        dominant_movement_pattern=dominant_movement_pattern,
        novelty=novelty,
        estimated_sets=estimated_sets,
    )


def strength_log(when: datetime, **kw: Any) -> WorkoutLog:
    """A hard-ish strength session (RPE ~7.5, 2 RIR, ~6 sets)."""
    params: dict[str, Any] = {
        "session_rpe": 7.5,
        "avg_rir": 2.0,
        "estimated_sets": 6.0,
        "total_volume_load": 6000.0,
        "dominant_movement_pattern": "squat",
    }
    params.update(kw)
    return make_log(when, "Strength", **params)


def aerobic_log(when: datetime, **kw: Any) -> WorkoutLog:
    """An easy aerobic run (RPE ~5, ~8 km)."""
    params: dict[str, Any] = {
        "duration_minutes": 50.0,
        "session_rpe": 5.0,
        "distance_meters": 8000.0,
        "dominant_movement_pattern": "run",
    }
    params.update(kw)
    return make_log(when, "Running", **params)


def conditioning_log(when: datetime, **kw: Any) -> WorkoutLog:
    """A glycolytic conditioning / mixed session (RPE ~8.5)."""
    params: dict[str, Any] = {
        "duration_minutes": 40.0,
        "session_rpe": 8.5,
    }
    params.update(kw)
    return make_log(when, "Mixed", **params)


def rest_dose() -> StressDose:
    """A zero stress dose — models a day of pure rest / time passing."""
    return StressDose()


# ---------------------------------------------------------------------------
# Driving the engine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    at: datetime
    log: WorkoutLog


def weekly_block(
    schedule: Sequence[tuple[int, LogFactory]],
    *,
    weeks: int,
    start: datetime | None = None,
) -> list[Step]:
    """Expand a weekly ``(day_of_week_offset, log_factory)`` schedule over N weeks.

    ``day_of_week_offset`` is 0-indexed from each week's start (0 = Mon-ish).
    """
    start = start or _EPOCH
    steps: list[Step] = []
    for w in range(weeks):
        week_start = start + timedelta(days=7 * w)
        for day_offset, factory in schedule:
            when = week_start + timedelta(days=day_offset)
            steps.append(Step(at=when, log=factory(when)))
    return steps


def run_schedule(
    state: UnifiedStateVector,
    steps: Sequence[Step],
) -> list[UnifiedStateVector]:
    """Run a schedule through dose → state-update, returning the full trajectory.

    The returned list starts with the input ``state`` and has one entry per step.
    Each step computes the dose with the real engine, so the trajectory reflects
    actual dose-law + state dynamics, not hand-built doses.
    """
    traj = [state]
    cur = state
    for step in sorted(steps, key=lambda s: s.at):
        dt = step.at - cur.timestamp
        dose = calculate_stress_dose(step.log)
        cur = update_athlete_state(cur, dose, dt, step.log)
        traj.append(cur)
    return traj


def rest_for(
    state: UnifiedStateVector,
    *,
    days: float,
    chunk_days: float = 7.0,
) -> list[UnifiedStateVector]:
    """Advance time with no training (zero dose) in chunks, returning trajectory.

    Used to model a layoff / detraining window. Chunking keeps decay numerically
    well-behaved over long spans.
    """
    traj = [state]
    cur = state
    remaining = days
    log = make_log(cur.timestamp, "Strength", session_rpe=1.0)
    while remaining > 1e-9:
        step_days = min(chunk_days, remaining)
        log = log.model_copy(update={"timestamp": cur.timestamp + timedelta(days=step_days)})
        cur = update_athlete_state(cur, rest_dose(), timedelta(days=step_days), log)
        traj.append(cur)
        remaining -= step_days
    return traj


def apply_benchmark(
    state: UnifiedStateVector,
    *,
    raw_value: float,
    mappings: Sequence[Any],
    better_direction: str = "higher",
    normalized_value: float | None = None,
    observation_weight: float = 1.0,
    observed_at: datetime | None = None,
) -> UnifiedStateVector:
    """Apply a single benchmark observation through the real assimilation path."""
    return apply_benchmark_observation(
        state,
        raw_value=raw_value,
        normalized_value=normalized_value,
        better_direction=better_direction,
        observation_weight=observation_weight,
        mappings=mappings,
        observed_at=observed_at or state.timestamp,
    )


# ---------------------------------------------------------------------------
# Trajectory inspection helpers
# ---------------------------------------------------------------------------

def capacity_series(traj: Sequence[UnifiedStateVector], axis: str) -> list[float]:
    return [float(getattr(s.capacity_x, axis)) for s in traj]


def fatigue_series(traj: Sequence[UnifiedStateVector], axis: str) -> list[float]:
    return [float(getattr(s.fatigue_f, axis)) for s in traj]


def capacity_gain(traj: Sequence[UnifiedStateVector], axis: str) -> float:
    """Net change in a capacity axis from the first to the last trajectory state."""
    series = capacity_series(traj, axis)
    return series[-1] - series[0]
