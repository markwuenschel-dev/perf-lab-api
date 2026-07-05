from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.benchmark_validity import effective_variance, get_validity_profile
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.observation import MappingSpec, build_observation, update
from app.logic.ekf.state_packing import INDEX_OF_KEY
from app.logic.ekf.transition import TransitionContext, predict
from app.ml.q10_confidence.ekf_calibration import (
    EkfUpdateRecord,
    calibration_report,
    interval_coverage,
    nis_consistency,
)
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


def _state() -> UnifiedStateVector:
    cx = CapacityState(max_strength=70.0)
    f = FatigueState(cns=20.0, muscular=20.0)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


# --- deterministic unit tests of the calibration statistics ---

def test_nis_consistency_perfect_is_within_band():
    recs = [EkfUpdateRecord(nis=1.0, n_obs=1) for _ in range(60)]
    out = nis_consistency(recs)
    assert abs(out["ratio"] - 1.0) < 1e-9
    assert out["within_chi2"] is True


def test_nis_consistency_overconfident_fails():
    recs = [EkfUpdateRecord(nis=10.0, n_obs=1) for _ in range(60)]
    out = nis_consistency(recs)
    assert out["ratio"] > 5.0
    assert out["within_chi2"] is False


def test_interval_coverage_all_inside_reads_full():
    recs = [EkfUpdateRecord(nis=1.0, n_obs=1, predicted_std=1.0, predicted_mean=0.5, realized=0.5)
            for _ in range(50)]
    cov = interval_coverage(recs)
    assert cov[0.50] == 1.0 and cov[0.95] == 1.0


def test_report_stay_shadow_when_overconfident():
    recs = [EkfUpdateRecord(nis=8.0, n_obs=1) for _ in range(60)]
    report = calibration_report(recs)
    assert report.verdict == "stay_shadow"
    assert any("NIS ratio" in r for r in report.reasons)


# --- Monte-Carlo self-consistency: observations drawn from the filter's own
#     predictive distribution must yield a χ²-consistent, well-covered filter. ---

def test_filter_is_self_consistent_under_matched_observations():
    rng = np.random.default_rng(0)
    p = default_parameters()
    s = _state()
    profile = get_validity_profile("1rm")
    i = INDEX_OF_KEY[("capacity", "max_strength")]
    m = profile.mapping_strength["max_strength"]
    r_axis = effective_variance(profile, s) / (m * m)
    specs = [MappingSpec(target_vector="capacity", target_key="max_strength", coefficient=1.0)]

    log = WorkoutLog(timestamp=datetime.now(UTC), modality="Strength", duration_minutes=45.0,
                     session_rpe=5.0, sleep_quality=7.0, life_stress_inverse=7.0)
    dose = StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())
    ctx = TransitionContext(dose=dose, time_delta=timedelta(days=1), log=log, template=s)

    belief = EkfBelief.seed_from_unified(s, p)
    records: list[EkfUpdateRecord] = []
    for _ in range(160):
        mean_k = float(belief.mean[i])
        var_pred = float(belief.cov[i, i]) + r_axis  # innovation variance S
        realized = float(np.clip(rng.normal(mean_k, math.sqrt(var_pred)), 0.0, 1.0))
        res = update(belief, build_observation(specs, profile, s, realized), p)
        records.append(EkfUpdateRecord(
            nis=res.nis, n_obs=1,
            predicted_std=math.sqrt(var_pred), predicted_mean=mean_k, realized=realized,
        ))
        belief = predict(res.belief, ctx, p)  # re-inflate/evolve between measurements

    out = nis_consistency(records)
    assert 0.6 <= out["ratio"] <= 1.6, f"NIS ratio {out['ratio']} outside consistency band"
    cov = interval_coverage(records)
    assert abs(cov[0.95] - 0.95) < 0.12
    assert abs(cov[0.50] - 0.50) < 0.15
