from __future__ import annotations

from datetime import UTC, datetime

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.tissue_risk import TissueRiskPrediction, compute_tissue_risk
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state(lumbar: float = 0.0, knee: float = 0.0, shoulder: float = 0.0) -> UnifiedStateVector:
    cx = CapacityState()
    f = FatigueState()
    t = TissueState(lumbar=lumbar, knee=knee, shoulder=shoulder)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.0,
        skill_state={},
        **leg,
    )


def test_fresh_state_green_for_all_axes():
    s = _state()
    result = compute_tissue_risk(s)
    assert isinstance(result, TissueRiskPrediction)
    for axis, tier in result.tier_by_axis.items():
        assert tier == "green", f"Fresh state: {axis} should be green, got {tier}"


def test_high_tissue_state_raises_risk():
    s = _state(lumbar=75.0)
    result = compute_tissue_risk(s)
    assert result.risk_by_axis["lumbar"] > 0.3, "High lumbar tissue should raise risk"
    assert result.tier_by_axis["lumbar"] in ("amber", "red")


def test_ac_ratio_spike_raises_risk():
    baseline = compute_tissue_risk(_state()).risk_by_axis["knee"]
    result = compute_tissue_risk(
        _state(),
        lagged_exposure_7d={"knee": 80.0},
        lagged_exposure_28d={"knee": 40.0},  # 7d = 2x chronic/4 = 2x ac_ratio > 1.3
    )
    spiked = result.risk_by_axis["knee"]
    assert spiked > baseline, "ACWR spike must raise knee risk above its no-spike baseline"


def test_prior_pain_increases_risk():
    s = _state()
    result_no_pain = compute_tissue_risk(s)
    result_with_pain = compute_tissue_risk(s, prior_pain_axes={"shoulder"})
    assert result_with_pain.risk_by_axis["shoulder"] > result_no_pain.risk_by_axis["shoulder"]


def test_shadow_only_flag():
    result = compute_tissue_risk(_state())
    assert result.shadow_only is True
    assert result.calibrated is False


def test_unknown_skip_not_a_tissue_event():
    """Tissue risk module must not infer tissue events from unknown skips."""
    compute_tissue_risk(_state())
    # The module only uses explicit exposure data, never infers from skip labels.
    # This is a design test — verify no skip-based logic exists in the module.
    import inspect

    import app.logic.tissue_risk as mod

    src = inspect.getsource(mod)
    assert "skip" not in src.lower(), "tissue_risk.py must not reference skip labels as tissue evidence"
