from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.benchmark_validity import get_validity_profile
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.observation import MappingSpec, build_observation, update
from app.logic.ekf.state_packing import INDEX_OF_KEY
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(cns=0.0, muscular=0.0) -> UnifiedStateVector:
    cx = CapacityState(max_strength=70.0, hypertrophy=55.0, power=50.0, mobility=50.0)
    f = FatigueState(cns=cns, muscular=muscular)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def _belief_with_correlation(a: str, b: str, rho: float) -> EkfBelief:
    """Seed belief, then inject correlation ρ between capacity axes a and b."""
    p = default_parameters()
    belief = EkfBelief.seed_from_unified(_state(), p)
    ia, ib = INDEX_OF_KEY[("capacity", a)], INDEX_OF_KEY[("capacity", b)]
    cov = belief.cov.copy()
    cov[ia, ib] = cov[ib, ia] = rho * np.sqrt(cov[ia, ia] * cov[ib, ib])
    belief.cov = cov
    return belief


def _specs(*keys: str) -> list[MappingSpec]:
    return [MappingSpec(target_vector="capacity", target_key=k, coefficient=1.0) for k in keys]


def test_update_shrinks_observed_and_correlated_axes():
    p = default_parameters()
    belief = _belief_with_correlation("max_strength", "hypertrophy", rho=0.7)
    v0 = belief.variances().copy()
    obs = build_observation(_specs("max_strength"), get_validity_profile("1rm"), _state(), score01=0.85)
    assert obs is not None
    res = update(belief, obs, p)
    v1 = res.belief.variances()
    i_ms = INDEX_OF_KEY[("capacity", "max_strength")]
    i_hy = INDEX_OF_KEY[("capacity", "hypertrophy")]
    i_mob = INDEX_OF_KEY[("capacity", "mobility")]
    assert v1[i_ms] < v0[i_ms] - 1e-6, "observed axis variance must shrink"
    assert v1[i_hy] < v0[i_hy] - 1e-6, "correlated axis variance must shrink"
    assert abs(v1[i_mob] - v0[i_mob]) < 1e-6, "uncorrelated axis must be ~unchanged"


def test_update_reduces_to_production_scalar_form():
    """Single axis, diagonal P → EKF matches the closed-form scalar residual anchor."""
    from app.logic.benchmark_validity import effective_variance

    p = default_parameters()
    s = _state()
    belief = EkfBelief.seed_from_unified(s, p)
    profile = get_validity_profile("1rm")
    i = INDEX_OF_KEY[("capacity", "max_strength")]
    score01 = 0.9

    obs = build_observation(_specs("max_strength"), profile, s, score01=score01)
    res = update(belief, obs, p)

    P = belief.cov[i, i]
    m = profile.mapping_strength["max_strength"]
    r_eff = effective_variance(profile, s)
    r_axis = r_eff / (m * m)
    gain = P / (P + r_axis)
    expected_mean = belief.mean[i] + gain * (score01 - belief.mean[i])
    expected_var = (1.0 - gain) * P

    assert abs(res.belief.mean[i] - expected_mean) < 1e-9
    assert abs(res.belief.variances()[i] - expected_var) < 1e-6


def test_fatigue_raises_measurement_noise_and_lowers_gain():
    p = default_parameters()
    profile = get_validity_profile("rep_max")  # fatigue-sensitive
    belief_fresh = EkfBelief.seed_from_unified(_state(), p)
    belief_tired = EkfBelief.seed_from_unified(_state(cns=70.0, muscular=70.0), p)
    obs_fresh = build_observation(_specs("hypertrophy"), profile, _state(), 0.6)
    obs_tired = build_observation(_specs("hypertrophy"), profile, _state(cns=70.0, muscular=70.0), 0.6)
    g_fresh = update(belief_fresh, obs_fresh, p).gain_norm
    g_tired = update(belief_tired, obs_tired, p).gain_norm
    assert g_tired < g_fresh, "fatigued benchmark → larger R → smaller gain"


def test_multi_axis_benchmark_updates_all_mapped_axes():
    p = default_parameters()
    belief = EkfBelief.seed_from_unified(_state(), p)
    v0 = belief.variances().copy()
    obs = build_observation(_specs("max_strength", "hypertrophy", "power"), get_validity_profile("1rm"), _state(), 0.8)
    assert obs is not None and obs.H.shape[0] == 3
    v1 = update(belief, obs, p).belief.variances()
    for k in ("max_strength", "hypertrophy", "power"):
        i = INDEX_OF_KEY[("capacity", k)]
        assert v1[i] < v0[i] - 1e-6, f"{k} should shrink under a multi-axis benchmark"


def test_covariance_stays_psd_over_many_updates():
    p = default_parameters()
    belief = EkfBelief.seed_from_unified(_state(), p)
    profile = get_validity_profile("1rm")
    for n in range(50):
        score = 0.5 + 0.4 * ((n % 5) / 5.0)
        obs = build_observation(_specs("max_strength", "hypertrophy"), profile, _state(), score)
        belief = update(belief, obs, p).belief
    assert np.min(np.linalg.eigvalsh(belief.cov)) >= -1e-8


def test_build_observation_returns_none_without_score():
    obs = build_observation(_specs("max_strength"), get_validity_profile("1rm"), _state(), score01=None)
    assert obs is None
