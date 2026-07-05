from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.ekf.state_packing import (
    AXIS_SCALE,
    CAPACITY_SLICE,
    FATIGUE_SLICE,
    N_STATE,
    STATE_KEYS,
    TISSUE_SLICE,
    pack,
    unpack,
)
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    cx = CapacityState(aerobic=400.0, max_strength=80.0, power=60.0, hypertrophy=55.0)
    f = FatigueState(cns=40.0, muscular=30.0, metabolic=50.0, structural=20.0)
    t = TissueState(knee=25.0, lumbar=15.0)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=3.0, habit_strength=0.4, skill_state={"squat": 0.5}, **leg,
    )


def test_layout_is_22_dims_and_ordered():
    assert N_STATE == 22
    assert len(STATE_KEYS) == 22
    assert [d for d, _ in STATE_KEYS[CAPACITY_SLICE]] == ["capacity"] * 8
    assert [d for d, _ in STATE_KEYS[FATIGUE_SLICE]] == ["fatigue"] * 6
    assert [d for d, _ in STATE_KEYS[TISSUE_SLICE]] == ["tissue"] * 8


def test_pack_is_normalized_to_unit_range():
    v = pack(_state())
    assert v.shape == (22,)
    assert np.all(v >= 0.0)
    assert np.all(v <= 1.0)
    # aerobic uses a 650 ceiling; 400/650 ≈ 0.615
    assert abs(v[0] - 400.0 / 650.0) < 1e-9


def test_pack_unpack_round_trip_preserves_xft():
    s = _state()
    s2 = unpack(pack(s), s)
    for domain, key in STATE_KEYS:
        attr = {"capacity": "capacity_x", "fatigue": "fatigue_f", "tissue": "tissue_t"}[domain]
        a = getattr(getattr(s, attr), key)
        b = getattr(getattr(s2, attr), key)
        assert abs(a - b) < 1e-6, f"{domain}.{key}: {a} != {b}"


def test_unpack_preserves_auxiliary_fields_from_template():
    s = _state()
    s2 = unpack(pack(s), s)
    assert s2.s_struct_signal == s.s_struct_signal
    assert s2.skill_state == s.skill_state
    assert s2.habit_strength == s.habit_strength


def test_axis_scale_matches_ceilings():
    assert AXIS_SCALE[0] == 650.0  # aerobic
    assert AXIS_SCALE[1] == 100.0  # glycolytic
    assert np.all(AXIS_SCALE[FATIGUE_SLICE] == 100.0)
    assert np.all(AXIS_SCALE[TISSUE_SLICE] == 100.0)
