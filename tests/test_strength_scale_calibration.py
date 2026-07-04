"""max_strength axis calibration (deferred follow-up: fix the ceiling saturation).

Non-DB: pins the c_nm_force <-> max_strength affine so the experience-prior seed
and the squat e1RM benchmark anchor (pl_e1rm_squat: floor 40kg, cap 250kg) stay on
ONE scale. Previously max_strength = c_nm_force/10 pegged any >=100kg squatter at
the 100 ceiling (no headroom, no level differentiation).
"""
import pytest

from app.domain.vectors import CapacityState, FatigueState, TissueState
from app.engine.state_bridge import (
    STRENGTH_FLOOR_CNM,
    STRENGTH_SLOPE_CNM,
    capacity_from_legacy,
    sync_legacy_from_vectors,
)


def _ms(c_nm_force: float) -> float:
    return capacity_from_legacy(300.0, c_nm_force, 100.0, 15000.0).max_strength


@pytest.mark.parametrize(
    "c_nm_force, expected",
    [
        (500.0, 4.76),    # beginner  (squat 50kg)
        (1000.0, 28.57),  # intermediate (squat 100kg) — was 100 (ceiling)
        (1800.0, 66.67),  # advanced  (squat 180kg)
        (2500.0, 100.0),  # elite     (squat 250kg = benchmark cap)
    ],
)
def test_experience_levels_are_differentiated_with_headroom(c_nm_force: float, expected: float):
    assert _ms(c_nm_force) == pytest.approx(expected, abs=0.05)


def test_intermediate_is_not_at_the_ceiling():
    # The bug: a 100kg squat pegged the axis. Now it leaves real headroom.
    assert _ms(1000.0) < 100.0


def test_seed_matches_the_squat_benchmark_score01():
    # pl_e1rm_squat standardizes squat_kg to [0,1] with floor 40, cap 250.
    # c_nm_force = squat_kg * 10, so the seeded axis must equal score01 * 100 —
    # otherwise a logged squat would yank the freshly-seeded value.
    for squat_kg in (100.0, 145.0, 250.0):
        score01 = (squat_kg - 40.0) / (250.0 - 40.0)
        assert _ms(squat_kg * 10.0) == pytest.approx(score01 * 100.0, abs=0.05)


def test_power_has_headroom_and_sits_below_max_strength():
    cap = capacity_from_legacy(300.0, 2500.0, 100.0, 15000.0)  # elite
    assert cap.power == pytest.approx(75.0, abs=0.1)  # not saturated
    assert cap.power < cap.max_strength


def test_legacy_mirror_round_trips():
    x = CapacityState(max_strength=45.0, aerobic=300.0)
    legacy = sync_legacy_from_vectors(x, FatigueState(), TissueState())
    assert legacy["c_nm_force"] == pytest.approx(45.0 * STRENGTH_SLOPE_CNM + STRENGTH_FLOOR_CNM)
    assert _ms(legacy["c_nm_force"]) == pytest.approx(45.0, abs=0.01)
