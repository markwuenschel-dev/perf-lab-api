"""INT-05 — structural/tendon safety decision reads components, not the lossy blend.

Design D1 (ADR-pending): explicit component bands + conjunctive joint rule, under
`structural_fatigue_safety_policy_v1`. The critical Recovery branch is evaluated
before the milder Tissue Deload and suppresses it (exactly one structural/tendon
safety candidate). Grip / tissue-average / f_struct_damage have no authority.

Pure function — no DB. Values are fatigue components (0–100).
"""
from datetime import datetime

import pytest

from app.domain.vectors import CapacityState, FatigueState, TissueState
from app.engine.state_bridge import build_unified_state_vector
from app.logic.prescriber import _safety_candidates

_TS = datetime(2026, 1, 1)
# The structural/tendon safety family (Recovery override + the milder Tissue Deload).
_RECOVERY = "safety_structural_damage"
_DELOAD = "safety_tendon_structural"
_FAMILY = {_RECOVERY, _DELOAD}


def _state(*, structural=0.0, tendon=0.0, grip=0.0, tissue=0.0):
    f = FatigueState(structural=structural, tendon=tendon, grip=grip)
    t = TissueState(
        shoulder=tissue, elbow=tissue, wrist=tissue, lumbar=tissue,
        hip=tissue, knee=tissue, ankle=tissue, finger=tissue,
    )
    return build_unified_state_vector(timestamp=_TS, x=CapacityState(), f=f, t=t)


def _family(cands):
    return [c for c in cands if c.branch_id in _FAMILY]


def _family_ids(cands):
    return [c.branch_id for c in _family(cands)]


# --- component-critical triggers ------------------------------------------------

def test_structural_critical_triggers_recovery():
    cands = _safety_candidates(_state(structural=85.0))
    assert _family_ids(cands) == [_RECOVERY]
    assert "Structural fatigue critical" in _family(cands)[0].rationale


def test_tendon_critical_triggers_recovery():
    cands = _safety_candidates(_state(tendon=75.0))
    assert _family_ids(cands) == [_RECOVERY]
    assert "Tendon fatigue critical" in _family(cands)[0].rationale


def test_jointly_high_triggers_recovery():
    cands = _safety_candidates(_state(structural=72.0, tendon=62.0))
    assert _family_ids(cands) == [_RECOVERY]
    assert "jointly elevated" in _family(cands)[0].rationale


# --- inclusive boundaries -------------------------------------------------------

def test_structural_inclusive_boundary():
    assert _family_ids(_safety_candidates(_state(structural=80.0))) == [_RECOVERY]


def test_tendon_inclusive_boundary():
    assert _family_ids(_safety_candidates(_state(tendon=70.0))) == [_RECOVERY]


def test_joint_inclusive_boundary():
    # S=70, T=60 — both exactly at the joint thresholds → Recovery.
    assert _family_ids(_safety_candidates(_state(structural=70.0, tendon=60.0))) == [_RECOVERY]


# --- non-triggers fall through to the milder Tissue Deload ----------------------

def test_below_critical_falls_to_deload_structural_side():
    # S=79 (<80), T=59 (<60 joint) → no Recovery; tendon 59 > 55 → Tissue Deload.
    assert _family_ids(_safety_candidates(_state(structural=79.0, tendon=59.0))) == [_DELOAD]


def test_below_joint_falls_to_deload_tendon_side():
    # S=69 (<70 joint), T=61 → no Recovery; tendon 61 > 55 → Tissue Deload.
    assert _family_ids(_safety_candidates(_state(structural=69.0, tendon=61.0))) == [_DELOAD]


# --- grip / tissue have NO authority over the structural/tendon decision --------

def test_grip_and_tissue_noise_do_not_trigger_structural_recovery():
    # Blend would exceed 70, but neither component is critical → no family candidate.
    cands = _safety_candidates(_state(structural=40.0, tendon=35.0, grip=100.0, tissue=100.0))
    assert _family_ids(cands) == []


@pytest.mark.parametrize("grip", [0.0, 50.0, 100.0])
@pytest.mark.parametrize("tissue", [0.0, 50.0, 100.0])
@pytest.mark.parametrize("structural,tendon", [(85.0, 0.0), (72.0, 62.0), (40.0, 35.0)])
def test_grip_tissue_invariance(structural, tendon, grip, tissue):
    # For fixed structural/tendon, sweeping grip/tissue cannot change the decision.
    baseline = _family_ids(_safety_candidates(_state(structural=structural, tendon=tendon)))
    swept = _family_ids(
        _safety_candidates(_state(structural=structural, tendon=tendon, grip=grip, tissue=tissue))
    )
    assert swept == baseline


# --- exactly one structural/tendon safety candidate -----------------------------

def test_max_both_emits_exactly_one_candidate():
    assert _family_ids(_safety_candidates(_state(structural=100.0, tendon=100.0))) == [_RECOVERY]


def test_high_both_no_recovery_deload_duplicate():
    # S=85, T=75 — must be exactly one Recovery, never Recovery + Tissue Deload.
    ids = _family_ids(_safety_candidates(_state(structural=85.0, tendon=75.0)))
    assert ids == [_RECOVERY]


# --- T3: counterfactual telemetry (legacy blend vs component decision) ----------

def test_counterfactual_classification_truth_table():
    from app.logic.prescriber import _classify_counterfactual
    assert _classify_counterfactual(True, True) == "both"
    assert _classify_counterfactual(True, False) == "legacy_only"
    assert _classify_counterfactual(False, True) == "component_only"
    assert _classify_counterfactual(False, False) == "neither"


def test_grip_noise_is_legacy_only_and_does_not_change_candidates():
    from app.logic.prescriber import _structural_recovery_trigger
    # Grip-noise: the legacy blend (40+35+0.15·100 = 90) would fire, the component
    # decision does not → this is a legacy_only case, and telemetry must not alter output.
    assert _structural_recovery_trigger(40.0, 35.0) is None
    cands = _safety_candidates(_state(structural=40.0, tendon=35.0, grip=100.0))
    assert _family_ids(cands) == []  # telemetry has no veto over the component decision


def test_tendon_at_threshold_is_component_only():
    from app.logic.prescriber import _structural_recovery_trigger
    # S=0, T=70: legacy blend = 70 (NOT > 70) but tendon is component-critical (>=70).
    assert _structural_recovery_trigger(0.0, 70.0) == "tendon_critical"
    assert _family_ids(_safety_candidates(_state(tendon=70.0))) == [_RECOVERY]
