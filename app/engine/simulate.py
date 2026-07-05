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
# Planned-intent → synthetic session log
# ---------------------------------------------------------------------------

# Per-session baseline load fields by modality (scaled by an intent's volume factor).
SESSION_BASELINES: dict[str, dict[str, Any]] = {
    "Strength": {"duration_minutes": 60.0, "estimated_sets": 6.0, "total_volume_load": 6000.0,
                 "dominant_movement_pattern": "squat"},
    "Hypertrophy": {"duration_minutes": 60.0, "estimated_sets": 8.0, "total_volume_load": 5000.0,
                    "dominant_movement_pattern": "squat"},
    "Power": {"duration_minutes": 55.0, "estimated_sets": 5.0, "total_volume_load": 3200.0,
              "dominant_movement_pattern": "squat"},
    "Running": {"duration_minutes": 50.0, "distance_meters": 8000.0, "dominant_movement_pattern": "run"},
    "Mixed": {"duration_minutes": 40.0, "dominant_movement_pattern": "mixed"},
}

# intensity -> (target RPE, avg RIR for lifting, load multiplier).
INTENSITY_BANDS: dict[str, dict[str, float]] = {
    "easy": {"rpe": 5.5, "rir": 4.0, "load": 0.85},
    "balanced": {"rpe": 7.0, "rir": 2.5, "load": 1.0},
    "hard": {"rpe": 8.5, "rir": 1.0, "load": 1.15},
}

# recovery -> (sleep quality, life-stress-inverse, volume multiplier). Poor recovery drives
# the dose engine's sleep/life penalties up (raises fatigue, lowers adaptation gain).
RECOVERY_BANDS: dict[str, dict[str, float]] = {
    "high": {"sleep": 8.0, "life": 8.0, "vol": 0.95},
    "standard": {"sleep": 7.0, "life": 7.0, "vol": 1.0},
    "minimal": {"sleep": 4.0, "life": 4.0, "vol": 1.05},
}


def session_log_from_intent(
    when: datetime,
    modality: str,
    *,
    scale: float,
    intensity: str,
    recovery: str,
) -> WorkoutLog:
    """One synthetic session log, scaled by a volume/intensity/recovery intent.

    The "planned intent → WorkoutLog" bridge: maps a (modality, intensity, recovery, scale)
    intent onto a synthetic ``WorkoutLog`` the real dose engine can consume. Reused by the
    projection service (weekly rollout) and the shadow MPC planner (ADR-0042).
    """
    intens = INTENSITY_BANDS.get(intensity, INTENSITY_BANDS["balanced"])
    rec = RECOVERY_BANDS.get(recovery, RECOVERY_BANDS["standard"])
    base = dict(SESSION_BASELINES.get(modality, SESSION_BASELINES["Mixed"]))

    eff_scale = max(0.1, scale)
    for field in ("duration_minutes", "estimated_sets", "total_volume_load", "distance_meters"):
        if field in base:
            base[field] = float(base[field]) * eff_scale

    # Effort-only sessions (Running / Mixed) leave avg_rir unset so the dose engine derives
    # failure-proximity from RPE; lifting sessions carry an explicit RIR.
    if modality in ("Strength", "Hypertrophy", "Power"):
        base["avg_rir"] = intens["rir"]

    return make_log(
        when, modality,  # type: ignore[arg-type]
        session_rpe=intens["rpe"], sleep_quality=rec["sleep"], life_stress_inverse=rec["life"],
        **base,
    )


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
