"""EKF predict: propagate the belief through the deterministic twin.

The transition model *is* ``update_athlete_state`` — we linearize it by finite
differences around the current mean, so the EKF's dynamics are, by construction,
identical to production's. The value the EKF adds is covariance propagation:
``P⁻ = A P Aᵀ + Q``, where ``A = ∂f/∂s`` carries fatigue-uncertainty into capacity
(the twin's adaptation efficiency and interference read the fatigue vector) and the
capacity cross-talk terms.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from app.engine.parameters import EngineParameters
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog

from .belief import EkfBelief
from .numerics import stabilize
from .params_vectors import process_noise_vector, variance_bounds
from .state_packing import N_STATE, pack, unpack


@dataclass
class TransitionContext:
    """Inputs to one transition step: the same dose/Δt/log production used, plus a
    template ``UnifiedStateVector`` supplying the non-covariance auxiliary fields
    (``s_struct_signal``, legacy mirrors, ``skill_state``)."""

    dose: StressDose
    time_delta: timedelta
    log: WorkoutLog
    template: UnifiedStateVector


def f_mean(vec: np.ndarray, ctx: TransitionContext) -> np.ndarray:
    """Advance a normalized state vector one step through ``update_athlete_state``."""
    prev = unpack(vec, ctx.template)
    nxt = update_athlete_state(prev, ctx.dose, ctx.time_delta, ctx.log)
    return pack(nxt)


def linearize_transition(
    vec: np.ndarray,
    ctx: TransitionContext,
    params: EngineParameters,
) -> np.ndarray:
    """Central-difference Jacobian ``A = ∂f/∂s`` (22x22) around ``vec``.

    Central differences (2N evals) give a symmetric, second-order-accurate estimate;
    at 22 dims this is ~44 cheap pure-Python ``f`` evaluations, well within a
    best-effort shadow write's budget.
    """
    eps = float(params.ekf_epsilon)
    A = np.empty((N_STATE, N_STATE), dtype=float)
    for j in range(N_STATE):
        plus = vec.copy()
        minus = vec.copy()
        plus[j] += eps
        minus[j] -= eps
        f_plus = f_mean(plus, ctx)
        f_minus = f_mean(minus, ctx)
        A[:, j] = (f_plus - f_minus) / (2.0 * eps)
    return A


def predict(
    belief: EkfBelief,
    ctx: TransitionContext,
    params: EngineParameters,
) -> EkfBelief:
    """One EKF predict step: mean through the twin, covariance ``A P Aᵀ + Q``.

    Q scales with elapsed days (process noise accrues with time, as in the production
    scalar path). The mean advance is *exactly* ``update_athlete_state`` — so the
    denormalized EKF mean matches the production engine when seeded identically.
    """
    dt_days = max(0.0, ctx.time_delta.total_seconds() / 86400.0)
    mean_pred = f_mean(belief.mean, ctx)
    A = linearize_transition(belief.mean, ctx, params)
    Q = np.diag(process_noise_vector(params) * dt_days)
    P_pred = A @ belief.cov @ A.T + Q

    lo, hi = variance_bounds(params)
    P_pred = stabilize(P_pred, lo, hi)
    return EkfBelief(
        mean=mean_pred,
        cov=P_pred,
        timestamp=belief.timestamp + ctx.time_delta,
        model_version=belief.model_version,
    )
