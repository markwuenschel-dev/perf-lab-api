from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.observation import build_wellness_observation, update
from app.logic.ekf.state_packing import INDEX_OF_KEY
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector

_P = default_parameters()


def _state(muscular=60.0, structural=60.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState(muscular=muscular, structural=structural)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_none_without_soreness():
    assert build_wellness_observation(SimpleNamespace(soreness=None), _P) is None
    assert build_wellness_observation(SimpleNamespace(), _P) is None


def test_soreness_targets_muscular_and_structural_fatigue():
    obs = build_wellness_observation(SimpleNamespace(soreness=6.0), _P)
    assert obs is not None
    assert obs.benchmark_code == "wellness"
    assert set(obs.axis_keys) == {"muscular", "structural"}
    assert obs.H.shape == (2, 22)
    assert np.allclose(obs.y, 0.6)  # soreness 6/10


def test_hrv_rhr_add_a_cns_autonomic_observation():
    obs = build_wellness_observation(SimpleNamespace(soreness=5.0, hrv_ms=40.0, resting_hr=68.0), _P)
    assert obs is not None
    assert set(obs.axis_keys) == {"muscular", "structural", "cns"}
    # poor autonomic readiness (low HRV, high RHR) → high observed CNS fatigue
    cns_i = list(obs.axis_keys).index("cns")
    assert obs.y[cns_i] > 0.6


def test_good_autonomic_readiness_low_cns_fatigue():
    obs = build_wellness_observation(SimpleNamespace(hrv_ms=90.0, resting_hr=45.0), _P)
    assert obs is not None and obs.axis_keys == ("cns",)
    assert obs.y[0] < 0.4  # well-recovered → low CNS fatigue


def test_update_shrinks_fatigue_variance_on_observed_axes():
    belief = EkfBelief.seed_from_unified(_state(), _P)
    i_mus = INDEX_OF_KEY[("fatigue", "muscular")]
    i_str = INDEX_OF_KEY[("fatigue", "structural")]
    i_cns = INDEX_OF_KEY[("fatigue", "cns")]
    v0 = belief.variances().copy()
    obs = build_wellness_observation(SimpleNamespace(soreness=7.0), _P)
    assert obs is not None
    res = update(belief, obs, _P)
    v1 = res.belief.variances()
    assert v1[i_mus] < v0[i_mus] - 1e-9   # observed axis shrinks
    assert v1[i_str] < v0[i_str] - 1e-9
    assert abs(v1[i_cns] - v0[i_cns]) < 1e-9  # unobserved axis unchanged (block-diagonal seed)
    assert res.trace_post < res.trace_pre
    assert np.isfinite(res.nis)
