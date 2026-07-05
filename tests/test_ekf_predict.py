from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.state_packing import AXIS_SCALE, INDEX_OF_KEY, STATE_KEYS, pack
from app.logic.ekf.transition import (
    TransitionContext,
    linearize_transition,
    predict,
)
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


def _state(cns=40.0, muscular=40.0, metabolic=40.0, structural=40.0) -> UnifiedStateVector:
    cx = CapacityState(max_strength=70.0, hypertrophy=55.0, power=50.0, aerobic=350.0)
    f = FatigueState(cns=cns, muscular=muscular, metabolic=metabolic, structural=structural,
                     tendon=20.0, grip=20.0)
    t = TissueState(knee=20.0, lumbar=20.0)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _log(sleep=7.0, stress=7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength",
        duration_minutes=60.0, session_rpe=6.0, sleep_quality=sleep, life_stress_inverse=stress,
    )


def _zero_dose() -> StressDose:
    return StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())


def _strength_dose() -> StressDose:
    return StressDose(
        dose_six=StressDoseSix(volume=1.0, intensity=1.0, density=0.5, impact=0.3, skill=0.2, metabolic=0.4),
        adaptation_contribution=AdaptationContribution(max_strength=5.0, hypertrophy=3.0),
        d_nm_central=4.0, d_nm_peripheral=3.0, d_met_systemic=2.0, d_struct_damage=1.0, d_struct_signal=3.0,
    )


def _ctx(state, dose, dt) -> TransitionContext:
    return TransitionContext(dose=dose, time_delta=dt, log=_log(), template=state)


def test_predict_mean_equals_production_engine():
    """The EKF mean advance IS update_athlete_state — zero model drift."""
    p = default_parameters()
    s = _state()
    dose = _strength_dose()
    dt = timedelta(days=2)
    belief = EkfBelief.seed_from_unified(s, p)
    predicted = predict(belief, _ctx(s, dose, dt), p)

    prod = update_athlete_state(s, dose, dt, _log())
    attr = {"capacity": "capacity_x", "fatigue": "fatigue_f", "tissue": "tissue_t"}
    prod_norm = np.array([getattr(getattr(prod, attr[d]), k) for d, k in STATE_KEYS]) / AXIS_SCALE
    assert np.allclose(predicted.mean, prod_norm, atol=1e-6)


def test_predict_covariance_is_symmetric_psd():
    p = default_parameters()
    s = _state()
    belief = EkfBelief.seed_from_unified(s, p)
    out = predict(belief, _ctx(s, _strength_dose(), timedelta(days=3)), p)
    assert np.allclose(out.cov, out.cov.T, atol=1e-10)
    assert np.min(np.linalg.eigvalsh(out.cov)) >= -1e-8


def test_predict_adds_process_noise_over_pure_propagation():
    """Q strictly adds uncertainty on top of A P Aᵀ."""
    p = default_parameters()
    s = _state()
    belief = EkfBelief.seed_from_unified(s, p)
    ctx = _ctx(s, _strength_dose(), timedelta(days=2))
    A = linearize_transition(belief.mean, ctx, p)
    apat = A @ belief.cov @ A.T
    out = predict(belief, ctx, p)
    assert np.trace(out.cov) > np.trace(apat) + 1e-9


def test_jacobian_decay_diagonal_matches_analytic():
    """A fatigue axis's self-derivative ≈ exp(-hours·m/τ) with zero dose."""
    p = default_parameters()
    s = _state()
    dt = timedelta(hours=24)
    ctx = _ctx(s, _zero_dose(), dt)
    A = linearize_transition(pack(s), ctx, p)
    idx = INDEX_OF_KEY[("fatigue", "metabolic")]
    tau = p.tau_fatigue_hours["metabolic"]
    expected = math.exp(-24.0 * 1.0 / tau)  # m≈1 at neutral sleep/stress
    assert abs(A[idx, idx] - expected) < 5e-3


def test_jacobian_has_fatigue_to_capacity_coupling():
    """Higher fatigue suppresses capacity adaptation → ∂X/∂F ≠ 0 (the info channel)."""
    p = default_parameters()
    s = _state()
    ctx = _ctx(s, _strength_dose(), timedelta(days=1))
    A = linearize_transition(pack(s), ctx, p)
    row = INDEX_OF_KEY[("capacity", "max_strength")]
    col_cns = INDEX_OF_KEY[("fatigue", "cns")]
    col_struct = INDEX_OF_KEY[("fatigue", "structural")]
    assert abs(A[row, col_cns]) > 1e-6 or abs(A[row, col_struct]) > 1e-6
