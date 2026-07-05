"""DB-free replay harness for the shadow EKF calibration gate (ADR-0041).

Drives a synthetic athlete through the **real** dose→twin pipeline while a shadow EKF and
the production scalar path track it in lockstep, so we can actually produce a calibration
verdict and answer the head-to-head question: does the joint-covariance EKF track
benchmarks at least as well as the per-axis scalar path, with calibrated uncertainty?

Generative model (self-consistent):
- Ground truth is a twin trajectory from a *true* baseline (`run` through the real
  `update_athlete_state`). Estimators start from a deliberately **wrong** seed baseline, so
  their means must converge from benchmark data.
- A benchmark on capacity axis k yields `realized = true_kᵢ + N(0, √R)` where
  `R = effective_variance(profile, state) / mapping_strength²` — the *same* variance the
  filter assumes. Observations are intentionally left unclamped (as in Q10's
  `synthesize_observations`) so the NIS/coverage relation stays exact. Calibration here
  therefore validates the covariance bookkeeping (predict propagation, Joseph updates, PSD
  projection), not robustness to a mis-specified R.

Why the EKF can beat the scalar path: benchmarks are single-axis (e.g. `max_strength`),
but the twin's `hypertrophy→max_strength` cross-talk gives the predict Jacobian an
off-diagonal term, so `P` develops a `max_strength↔hypertrophy` correlation. A
`max_strength` observation then also corrects the *untested* `hypertrophy` axis — which the
scalar per-mapping loop cannot — so a later `hypertrophy` benchmark is predicted better.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np

from app.engine.parameters import default_parameters
from app.engine.simulate import baseline_state, strength_log
from app.logic.benchmark_validity import (
    BenchmarkValidityProfile,
    effective_variance,
    get_validity_profile,
)
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.observation import MappingSpec, build_observation, update
from app.logic.ekf.state_packing import INDEX_OF_KEY, axis_scale
from app.logic.ekf.transition import TransitionContext, predict
from app.logic.state_update_v0 import apply_benchmark_observation, update_athlete_state
from app.ml.q10_confidence.ekf_calibration import EkfUpdateRecord
from app.schemas.state import UnifiedStateVector

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

# Benchmark code per capacity axis (each has a validity profile in benchmark_validity).
_CODE_FOR_AXIS = {"max_strength": "1rm", "hypertrophy": "rep_max"}


@dataclass
class BenchmarkEvent:
    at: datetime
    axis: str
    code: str


@dataclass
class _Workout:
    at: datetime
    log: Any


@dataclass
class ReplayResult:
    records: list[EkfUpdateRecord] = field(default_factory=list)


def _scalar_mapping(axis: str) -> SimpleNamespace:
    """A residual capacity mapping, shaped like an ORM ObservationMapping row."""
    return SimpleNamespace(
        target_vector="capacity", target_key=axis, mapping_type="residual",
        coefficient=1.0, intercept=0.0, min_value=None, max_value=None, config={},
    )


def default_scenario(
    *,
    weeks: int = 36,
    benchmark_every_days: int = 5,
    hypertrophy_every_n: int = 3,
) -> tuple[dict[str, float], dict[str, float], list[_Workout], list[BenchmarkEvent]]:
    """A strength athlete whose true max_strength/hypertrophy sit above the seed.

    Workouts: 3 strength sessions/week. Benchmarks: mostly `max_strength`, every
    `hypertrophy_every_n`-th one tests `hypertrophy` instead — so the correlated
    (untested-between) axis is periodically measured to expose the EKF's cross-axis gain.
    """
    seed_caps = {"max_strength": 55.0, "hypertrophy": 50.0}
    true_caps = {"max_strength": 80.0, "hypertrophy": 74.0}

    workouts: list[_Workout] = []
    for w in range(weeks):
        week_start = _EPOCH + timedelta(days=7 * w)
        for day in (0, 2, 4):
            when = week_start + timedelta(days=day)
            workouts.append(_Workout(at=when, log=strength_log(when)))

    benchmarks: list[BenchmarkEvent] = []
    horizon = 7 * weeks
    i = 0
    day = benchmark_every_days
    while day < horizon:
        axis = "hypertrophy" if (i % hypertrophy_every_n == hypertrophy_every_n - 1) else "max_strength"
        benchmarks.append(BenchmarkEvent(at=_EPOCH + timedelta(days=day), axis=axis, code=_CODE_FOR_AXIS[axis]))
        i += 1
        day += benchmark_every_days
    return seed_caps, true_caps, workouts, benchmarks


def run_replay(
    *,
    seed: int = 0,
    weeks: int = 36,
    benchmark_every_days: int = 5,
    hypertrophy_every_n: int = 3,
) -> ReplayResult:
    """Run the lockstep replay and return per-benchmark calibration records."""
    rng = np.random.default_rng(seed)
    params = default_parameters()
    seed_caps, true_caps, workouts, benchmarks = default_scenario(
        weeks=weeks, benchmark_every_days=benchmark_every_days, hypertrophy_every_n=hypertrophy_every_n
    )

    true_state = baseline_state(when=_EPOCH, **true_caps)
    scalar_state = baseline_state(when=_EPOCH, **seed_caps)
    belief = EkfBelief.seed_from_unified(baseline_state(when=_EPOCH, **seed_caps), params)

    events: list[tuple[datetime, str, Any]] = (
        [(w.at, "workout", w.log) for w in workouts]
        + [(b.at, "benchmark", b) for b in benchmarks]
    )
    events.sort(key=lambda e: (e[0], 0 if e[1] == "workout" else 1))

    result = ReplayResult()
    for at, kind, payload in events:
        if kind == "workout":
            dose = calculate_stress_dose(payload)
            true_state = update_athlete_state(true_state, dose, at - true_state.timestamp, payload)
            ctx = TransitionContext(
                dose=dose, time_delta=at - scalar_state.timestamp, log=payload, template=scalar_state
            )
            scalar_state = update_athlete_state(scalar_state, dose, at - scalar_state.timestamp, payload)
            belief = predict(belief, ctx, params)
        else:
            record, belief, scalar_state = _benchmark_step(
                payload, rng, params, belief, scalar_state, true_state
            )
            result.records.append(record)
    return result


def _benchmark_step(
    ev: BenchmarkEvent,
    rng: np.random.Generator,
    params: Any,
    belief: EkfBelief,
    scalar_state: UnifiedStateVector,
    true_state: UnifiedStateVector,
) -> tuple[EkfUpdateRecord, EkfBelief, UnifiedStateVector]:
    """Apply one benchmark to both estimators; return (record, new_belief, new_scalar_state)."""
    axis = ev.axis
    idx = INDEX_OF_KEY[("capacity", axis)]
    ceil = axis_scale("capacity", axis)
    profile: BenchmarkValidityProfile = get_validity_profile(ev.code)
    m = max(1e-3, float(profile.mapping_strength.get(axis, 1.0)))

    true01 = float(getattr(true_state.capacity_x, axis)) / ceil
    r_axis = effective_variance(profile, scalar_state) / (m * m)
    realized = true01 + float(rng.normal(0.0, math.sqrt(r_axis)))  # unclamped (see module docstring)

    ekf_pred = float(belief.mean[idx])
    scalar_pred = float(getattr(scalar_state.capacity_x, axis)) / ceil
    predicted_std = math.sqrt(float(belief.cov[idx, idx]) + r_axis)

    obs = build_observation([MappingSpec("capacity", axis, 1.0)], profile, scalar_state, realized)
    assert obs is not None
    res = update(belief, obs, params)

    new_scalar = apply_benchmark_observation(
        scalar_state, raw_value=realized, normalized_value=None, better_direction="higher",
        observation_weight=1.0, mappings=[_scalar_mapping(axis)], observed_at=ev.at,
        score01=realized, validity_profile=profile,
    )
    record = EkfUpdateRecord(
        nis=res.nis, n_obs=1, predicted_std=predicted_std, predicted_mean=ekf_pred,
        realized=realized, scalar_pred=scalar_pred, true_score=true01, axis=axis,
    )
    return record, res.belief, new_scalar
