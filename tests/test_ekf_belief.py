from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from psd_helpers import assert_covariance_psd

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.state_packing import INDEX_OF_KEY
from app.schemas.engine_vectors import CapacityConfidence, CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    cx = CapacityState(max_strength=80.0)
    f = FatigueState(cns=30.0)
    t = TissueState()
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        capacity_confidence=CapacityConfidence(max_strength=0.4, aerobic=0.9),
        s_struct_signal=0.0, habit_strength=0.0, skill_state={}, **leg,
    )


def test_seed_is_symmetric_psd_and_block_diagonal():
    p = default_parameters()
    b = EkfBelief.seed_from_unified(_state(), p)
    assert b.mean.shape == (22,)
    assert b.cov.shape == (22, 22)
    assert_covariance_psd(b.cov)
    # off-diagonal is zero at seed
    off = b.cov - np.diag(np.diag(b.cov))
    assert np.allclose(off, 0.0)


def test_seed_capacity_variance_comes_from_production_confidence():
    p = default_parameters()
    b = EkfBelief.seed_from_unified(_state(), p)
    i_ms = INDEX_OF_KEY[("capacity", "max_strength")]
    i_ae = INDEX_OF_KEY[("capacity", "aerobic")]
    assert abs(b.variances()[i_ms] - 0.4) < 1e-9
    assert abs(b.variances()[i_ae] - 0.9) < 1e-9


def test_seed_fatigue_tissue_variance_from_params():
    p = default_parameters()
    b = EkfBelief.seed_from_unified(_state(), p)
    i_cns = INDEX_OF_KEY[("fatigue", "cns")]
    i_knee = INDEX_OF_KEY[("tissue", "knee")]
    assert abs(b.variances()[i_cns] - p.ekf_seed_variance_fatigue) < 1e-9
    assert abs(b.variances()[i_knee] - p.ekf_seed_variance_tissue) < 1e-9


def test_jsonb_round_trip():
    p = default_parameters()
    b = EkfBelief.seed_from_unified(_state(), p)
    # perturb covariance so it's dense
    b.cov[0, 1] = b.cov[1, 0] = 0.05
    b2 = EkfBelief.from_row(
        mean_map=b.mean_map(),
        cov_list=b.cov_list(),
        timestamp=b.timestamp,
        model_version=b.model_version,
    )
    assert np.allclose(b.mean, b2.mean)
    assert np.allclose(b.cov, b2.cov)
    assert b2.model_version == b.model_version
