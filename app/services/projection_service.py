"""Goal-aware forward-projection engine (Phase 7 — goal-anchored program).

Simulates the athlete's 8 capacity axes over an N-week horizon under a
hypothetical plan, and against a steady "maintain" baseline for comparison.

The math is *not* reinvented here: this module is a thin driver that turns
projection params into a weekly sequence of synthetic ``WorkoutLog``s (the
goal -> weekly-dose bridge) and iterates the *real* engine primitives
``calculate_stress_dose`` -> ``update_athlete_state`` week by week. Interference
(ADR-0037) and detraining (ADR-0033) are already applied inside
``update_athlete_state``, so they come along for free.

``project_trajectory`` is a PURE function (state_in + params -> response): no DB,
no async, deterministic. The authed endpoint (``app.api.v1.simulate``) only loads
the current state and calls it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.vectors import CapacityState
from app.engine.simulate import make_log, rest_dose
from app.logic.constraint_engine import mean_fatigue, overall_readiness
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.planning import periodization_envelope
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.projection import (
    AxisProjection,
    ProjectionRequest,
    ProjectionResponse,
)
from app.schemas.state import UnifiedStateVector

# Deterministic clock the projection is rebased onto so the first session sees a
# clean zero elapsed time (a stale loaded-state timestamp must not trigger a
# months-long detraining decay on step one).
_PROJECTION_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

# Maintenance reference volume — the "just tick over" baseline the plan is compared
# against (also the divisor that centres the volume slider). The baseline runs at
# this volume, easy intensity, standard recovery, and no periodization: a modest
# steady floor an actively progressing plan should out-grow.
_MAINTAIN_VOLUME = 48

# Human labels for the 8 canonical capacity axes (CapacityState.KEYS order).
_AXIS_LABELS: dict[str, str] = {
    "aerobic": "Aerobic",
    "glycolytic": "Glycolytic",
    "max_strength": "Max strength",
    "hypertrophy": "Hypertrophy",
    "power": "Power",
    "skill": "Skill",
    "mobility": "Mobility",
    "work_capacity": "Work capacity",
}

# Goal -> weekly modality mix (one entry per session). Modalities are the engine's
# canonical set: Running | Strength | Hypertrophy | Power | Mixed. The dose engine
# shapes adaptation per modality, so the mix is what makes a Powerlifting plan grow
# max_strength while a Running plan grows aerobic.
_GOAL_MIX_DEFAULT = ["Strength", "Running", "Mixed", "Strength"]
_GOAL_MIX: dict[str, list[str]] = {
    "Strength": ["Strength", "Strength", "Strength", "Mixed"],
    "Powerlifting": ["Strength", "Strength", "Strength", "Mixed"],
    "OlympicLifts": ["Power", "Strength", "Power", "Mixed"],
    "Power": ["Power", "Power", "Strength", "Mixed"],
    "Hypertrophy": ["Hypertrophy", "Hypertrophy", "Hypertrophy", "Strength"],
    "Calisthenics": ["Strength", "Strength", "Mixed", "Mixed"],
    "Gymnastics": ["Strength", "Power", "Mixed", "Mixed"],
    "Grip": ["Strength", "Strength", "Mixed"],
    "MetCon": ["Mixed", "Mixed", "Running", "Strength"],
    "General": ["Strength", "Running", "Mixed", "Strength"],
    "Running": ["Running", "Running", "Running", "Mixed"],
    "Sprinting": ["Running", "Power", "Running", "Mixed"],
    "HalfMarathon": ["Running", "Running", "Running", "Running"],
    "FullMarathon": ["Running", "Running", "Running", "Running", "Running"],
}

# Per-session baseline load fields by modality (scaled by the weekly factor).
_BASE_SESSION: dict[str, dict[str, Any]] = {
    "Strength": {
        "duration_minutes": 60.0,
        "estimated_sets": 6.0,
        "total_volume_load": 6000.0,
        "dominant_movement_pattern": "squat",
    },
    "Hypertrophy": {
        "duration_minutes": 60.0,
        "estimated_sets": 8.0,
        "total_volume_load": 5000.0,
        "dominant_movement_pattern": "squat",
    },
    "Power": {
        "duration_minutes": 55.0,
        "estimated_sets": 5.0,
        "total_volume_load": 3200.0,
        "dominant_movement_pattern": "squat",
    },
    "Running": {
        "duration_minutes": 50.0,
        "distance_meters": 8000.0,
        "dominant_movement_pattern": "run",
    },
    "Mixed": {
        "duration_minutes": 40.0,
        "dominant_movement_pattern": "mixed",
    },
}

# intensity -> (target RPE, avg RIR for lifting, load multiplier).
_INTENSITY: dict[str, dict[str, float]] = {
    "easy": {"rpe": 5.5, "rir": 4.0, "load": 0.85},
    "balanced": {"rpe": 7.0, "rir": 2.5, "load": 1.0},
    "hard": {"rpe": 8.5, "rir": 1.0, "load": 1.15},
}

# recovery -> (sleep quality, life-stress-inverse, volume multiplier). Poor
# recovery (minimal) drives the dose engine's sleep/life penalties up, which both
# raises fatigue and *lowers* adaptation gain — modelling overreaching.
_RECOVERY: dict[str, dict[str, float]] = {
    "high": {"sleep": 8.0, "life": 8.0, "vol": 0.95},
    "standard": {"sleep": 7.0, "life": 7.0, "vol": 1.0},
    "minimal": {"sleep": 4.0, "life": 4.0, "vol": 1.05},
}

# A near-zero-dose "rest" log used to elapse the trailing recovery days of a week.
_REST_MODALITY = "Strength"


def _mix_for(goal: str) -> list[str]:
    return _GOAL_MIX.get(goal, _GOAL_MIX_DEFAULT)


def _week_sessions(mix: list[str], sessions_per_week: int) -> list[str]:
    """Cycle the goal's modality mix out to ``sessions_per_week`` entries.

    Volume has real traction through session *count* (each session is a discrete
    adaptation impulse) rather than per-session load, which the dose law compresses
    through ``log1p``.
    """
    n = max(1, sessions_per_week)
    return [mix[i % len(mix)] for i in range(n)]


def _build_session_log(
    when: datetime,
    modality: str,
    *,
    scale: float,
    intensity: str,
    recovery: str,
) -> Any:
    """One synthetic session log, scaled by the weekly volume/intensity/recovery."""
    intens = _INTENSITY.get(intensity, _INTENSITY["balanced"])
    rec = _RECOVERY.get(recovery, _RECOVERY["standard"])
    base = dict(_BASE_SESSION.get(modality, _BASE_SESSION["Mixed"]))

    eff_scale = max(0.1, scale)
    for field in ("duration_minutes", "estimated_sets", "total_volume_load", "distance_meters"):
        if field in base:
            base[field] = float(base[field]) * eff_scale

    # Effort-only sessions (Running / Mixed) leave avg_rir unset so the dose engine
    # derives failure-proximity from RPE; lifting sessions carry an explicit RIR.
    if modality in ("Strength", "Hypertrophy", "Power"):
        base["avg_rir"] = intens["rir"]

    return make_log(
        when,
        modality,  # type: ignore[arg-type]
        session_rpe=intens["rpe"],
        sleep_quality=rec["sleep"],
        life_stress_inverse=rec["life"],
        **base,
    )


def _run_week(
    state: UnifiedStateVector,
    mix: list[str],
    *,
    scale: float,
    intensity: str,
    recovery: str,
    week_start: datetime,
) -> tuple[UnifiedStateVector, list[UnifiedStateVector]]:
    """Drive one week of sessions + trailing recovery through the real engine.

    Returns the end-of-week state (post-recovery, used for weekly snapshots) and
    the list of intermediate post-session states (used for peak-fatigue sampling).
    """
    cur = state
    samples: list[UnifiedStateVector] = []
    n = max(1, len(mix))

    for i, modality in enumerate(mix):
        # Spread sessions across the first ~6 days, leaving a trailing recovery day.
        day = i * (6.0 / n)
        when = week_start + timedelta(days=day)
        log = _build_session_log(
            when, modality, scale=scale, intensity=intensity, recovery=recovery
        )
        dt = when - cur.timestamp
        if dt.total_seconds() < 0:
            dt = timedelta(0)
        dose = calculate_stress_dose(log)
        cur = update_athlete_state(cur, dose, dt, log)
        samples.append(cur)

    # Elapse the remainder of the 7-day week as pure rest so fatigue clears and the
    # weekly snapshot reflects a realistic between-week recovery.
    week_end = week_start + timedelta(days=7.0)
    rest_dt = week_end - cur.timestamp
    if rest_dt.total_seconds() > 0:
        rest_log = make_log(week_end, _REST_MODALITY, session_rpe=1.0)
        cur = update_athlete_state(cur, rest_dose(), rest_dt, rest_log)

    return cur, samples


def _simulate(
    state0: UnifiedStateVector,
    *,
    goal: str,
    weeks: int,
    weekly_volume: int,
    intensity: str,
    recovery: str,
    periodized: bool,
) -> tuple[list[UnifiedStateVector], float]:
    """Simulate week 0..N and return (weekly boundary states, peak mean-fatigue).

    ``periodized`` applies the ADR-0029 accumulation/intensification/peak/taper
    volume envelope; the maintain baseline runs flat (no periodization).
    """
    mix = _mix_for(goal)
    intens_load = _INTENSITY.get(intensity, _INTENSITY["balanced"])["load"]
    rec_vol = _RECOVERY.get(recovery, _RECOVERY["standard"])["vol"]

    # Per-session load reflects intensity/recovery only; weekly volume drives the
    # session *count* below (the lever the dose law doesn't log-compress).
    session_scale = intens_load * rec_vol
    base_sessions = len(mix)

    cur = state0.model_copy(update={"timestamp": _PROJECTION_EPOCH})
    boundary_states: list[UnifiedStateVector] = [cur]
    peak_fatigue = mean_fatigue(cur)

    for w in range(1, weeks + 1):
        env_mod = periodization_envelope(weeks, w).volume_modifier if periodized else 1.0
        raw = base_sessions * (weekly_volume / _MAINTAIN_VOLUME) * env_mod
        sessions_per_week = max(1, min(10, round(raw)))
        week_mix = _week_sessions(mix, sessions_per_week)
        cur, samples = _run_week(
            cur,
            week_mix,
            scale=session_scale,
            intensity=intensity,
            recovery=recovery,
            week_start=cur.timestamp,
        )
        for s in samples:
            peak_fatigue = max(peak_fatigue, mean_fatigue(s))
        boundary_states.append(cur)

    return boundary_states, peak_fatigue


def project_trajectory(
    state_in: UnifiedStateVector, req: ProjectionRequest
) -> ProjectionResponse:
    """Pure projection: current state + params -> full 8-axis trajectory.

    DB-free and deterministic. Produces the plan trajectory plus a maintain
    baseline for comparison, then packages both into the frozen response contract.
    """
    weeks = max(1, min(16, int(req.weeks)))
    weekly_volume = max(30, min(90, int(req.weekly_volume)))

    plan_states, peak_fatigue = _simulate(
        state_in,
        goal=req.goal,
        weeks=weeks,
        weekly_volume=weekly_volume,
        intensity=req.intensity,
        recovery=req.recovery,
        periodized=True,
    )
    baseline_states, _ = _simulate(
        state_in,
        goal=req.goal,
        weeks=weeks,
        weekly_volume=_MAINTAIN_VOLUME,
        intensity="easy",
        recovery="standard",
        periodized=False,
    )

    axes: list[AxisProjection] = []
    for key in CapacityState.KEYS:
        series = [round(float(getattr(s.capacity_x, key)), 1) for s in plan_states]
        baseline_series = [
            round(float(getattr(s.capacity_x, key)), 1) for s in baseline_states
        ]
        axes.append(
            AxisProjection(
                key=key,
                label=_AXIS_LABELS.get(key, key),
                start=series[0],
                projected=series[-1],
                baseline=baseline_series[-1],
                series=series,
                baseline_series=baseline_series,
            )
        )

    readiness_series = [
        round(overall_readiness(s) * 100.0, 1) for s in plan_states
    ]

    return ProjectionResponse(
        goal=req.goal,
        weeks=weeks,
        axes=axes,
        readiness_series=readiness_series,
        peak_fatigue=round(peak_fatigue, 1),
    )
