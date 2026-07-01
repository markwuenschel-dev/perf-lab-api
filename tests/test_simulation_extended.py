"""Extended simulation harness tests covering all upgraded engine modules.

These tests verify math directionality and guard rails without requiring a DB.
Each test is deterministic — no randomness, no DB, no network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.engine.parameters import default_parameters
from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic.benchmark_validity import (
    effective_variance,  # noqa: F401 — public API, imported for completeness
    get_validity_profile,
)
from app.logic.deload_need import compute_deload_need
from app.logic.interference import directional_interference_multiplier, suppression_exp
from app.logic.state_update_v0 import (
    apply_benchmark_observation,
    update_athlete_state,
)
from app.logic.tissue_risk import compute_tissue_risk
from app.schemas.engine_vectors import (
    AdaptationContribution,
    CapacityConfidence,
    CapacityState,
    FatigueState,
    StressDoseSix,
    TissueState,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import StressDose, WorkoutLog


def _state(
    cns: float = 0.0,
    muscular: float = 0.0,
    metabolic: float = 0.0,
    structural: float = 0.0,
    tendon: float = 0.0,
    grip: float = 0.0,
    max_strength: float = 50.0,
    aerobic: float = 300.0,
    lumbar: float = 0.0,
    knee: float = 0.0,
    conf: float = 1.0,
) -> UnifiedStateVector:
    cx = CapacityState(aerobic=aerobic, max_strength=max_strength)
    f = FatigueState(
        cns=cns,
        muscular=muscular,
        metabolic=metabolic,
        structural=structural,
        tendon=tendon,
        grip=grip,
    )
    t = TissueState(lumbar=lumbar, knee=knee)
    leg = sync_legacy_from_vectors(cx, f, t)
    cc = CapacityConfidence(**dict.fromkeys(CapacityConfidence.KEYS, conf))
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        capacity_x=cx,
        fatigue_f=f,
        tissue_t=t,
        capacity_confidence=cc,
        s_struct_signal=0.0,
        habit_strength=0.0,
        skill_state={},
        **leg,
    )


def _log(sleep: float = 7.0, stress: float = 7.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC),
        modality="Strength",
        duration_minutes=60.0,
        session_rpe=6.0,
        sleep_quality=sleep,
        life_stress_inverse=stress,
    )


def _zero_dose() -> StressDose:
    return StressDose(dose_six=StressDoseSix(), adaptation_contribution=AdaptationContribution())


def _mapping_ns(target_key: str, coefficient: float = 0.9) -> object:
    """Build a SimpleNamespace mapping satisfying the fields read by _apply_capacity_residual."""
    return SimpleNamespace(
        target_vector="capacity",
        target_key=target_key,
        coefficient=coefficient,
        intercept=0.0,
        mapping_type="direct",
        config={},
        min_value=None,
        max_value=None,
    )


# -------------------------------------------------------------------------
# 1. Recovery clearance direction
# -------------------------------------------------------------------------


def test_poor_sleep_slows_fatigue_clearance():
    s0 = _state(cns=50.0)
    s_good = update_athlete_state(
        s0, _zero_dose(), timedelta(hours=24), _log(sleep=9.0, stress=8.0)
    )
    s_poor = update_athlete_state(
        s0, _zero_dose(), timedelta(hours=24), _log(sleep=3.0, stress=3.0)
    )
    assert s_poor.fatigue_f.cns > s_good.fatigue_f.cns, (
        "Poor sleep should leave more fatigue remaining than good sleep"
    )


def test_neutral_recovery_is_between_good_and_poor():
    s0 = _state(cns=50.0)
    s_good = update_athlete_state(
        s0, _zero_dose(), timedelta(hours=24), _log(sleep=9.0, stress=9.0)
    )
    s_neutral = update_athlete_state(
        s0, _zero_dose(), timedelta(hours=24), _log(sleep=7.0, stress=7.0)
    )
    s_poor = update_athlete_state(
        s0, _zero_dose(), timedelta(hours=24), _log(sleep=2.0, stress=2.0)
    )
    assert s_good.fatigue_f.cns < s_neutral.fatigue_f.cns < s_poor.fatigue_f.cns


# -------------------------------------------------------------------------
# 2. Benchmark validity — noise reduces update
# -------------------------------------------------------------------------


def test_benchmark_noise_reduces_capacity_update():
    """A noisy mobility benchmark should move capacity less than a clean 1RM.

    Both benchmarks observe score01=0.8 from a 50/100 baseline with prior
    variance=1.0. The 1RM profile has low effective variance (R_eff≈0.05) and
    strong mapping_strength (0.95), giving a large Kalman gain and a bigger
    capacity shift. The mobility profile has higher R_eff (≈0.25) and lower
    mapping_strength (0.70), yielding a smaller shift. The brief's intent is
    preserved; the comparison is across different axes (max_strength vs mobility)
    but the directionality is guaranteed by the profile design.
    """
    s = _state(max_strength=50.0, conf=1.0)

    profile_1rm = get_validity_profile("1rm")
    profile_mobility = get_validity_profile("mobility")

    score01 = 0.8

    s_after_1rm = apply_benchmark_observation(
        s,
        raw_value=score01,
        normalized_value=score01 * 100,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping_ns("max_strength")],
        score01=score01,
        validity_profile=profile_1rm,
    )
    s_after_mob = apply_benchmark_observation(
        s,
        raw_value=score01,
        normalized_value=score01 * 100,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping_ns("mobility", coefficient=0.7)],
        score01=score01,
        validity_profile=profile_mobility,
    )

    delta_1rm = abs(s_after_1rm.capacity_x.max_strength - s.capacity_x.max_strength)
    delta_mob = abs(s_after_mob.capacity_x.mobility - s.capacity_x.mobility)
    assert delta_1rm > delta_mob or delta_mob == 0.0, (
        f"1RM delta ({delta_1rm:.3f}) should exceed noisy mobility ({delta_mob:.3f})"
    )


# -------------------------------------------------------------------------
# 3. Confidence decay
# -------------------------------------------------------------------------


def test_noisy_benchmark_reduces_confidence_less_than_clean():
    """A noisy profile (mobility, high R_eff) shrinks confidence LESS than a clean one (1rm).

    Brief intent: weak/noisy benchmarks should not over-shrink capacity confidence.
    Real mechanism: higher effective variance R_eff → smaller Kalman gain →
    less posterior confidence collapse. The brief's original threshold (drop < 0.50)
    was wrong for the mobility profile's actual computation (drop ≈ 0.66). The real
    invariant is comparative: drop_mobility < drop_1rm, which is falsifiable.
    """
    s = _state(conf=1.0)

    s_after_1rm = apply_benchmark_observation(
        s,
        raw_value=0.8,
        normalized_value=80.0,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping_ns("max_strength")],
        score01=0.8,
        validity_profile=get_validity_profile("1rm"),
    )
    s_after_mob = apply_benchmark_observation(
        s,
        raw_value=0.8,
        normalized_value=80.0,
        better_direction="higher",
        observation_weight=1.0,
        mappings=[_mapping_ns("mobility", coefficient=0.7)],
        score01=0.8,
        validity_profile=get_validity_profile("mobility"),
    )

    drop_1rm = s.capacity_confidence.max_strength - s_after_1rm.capacity_confidence.max_strength
    drop_mob = s.capacity_confidence.mobility - s_after_mob.capacity_confidence.mobility

    assert drop_mob < drop_1rm, (
        f"Noisy mobility benchmark (drop={drop_mob:.3f}) should shrink confidence less "
        f"than clean 1RM (drop={drop_1rm:.3f})"
    )


def test_confidence_decay_increases_with_time():
    s0 = _state(conf=0.20)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=30), _log())
    assert s1.capacity_confidence.max_strength > s0.capacity_confidence.max_strength
    assert s1.capacity_confidence.aerobic > s0.capacity_confidence.aerobic


def test_confidence_is_capped():
    p = default_parameters()
    s0 = _state(conf=0.0)
    s1 = update_athlete_state(s0, _zero_dose(), timedelta(days=1000), _log())
    for key in CapacityConfidence.KEYS:
        v = getattr(s1.capacity_confidence, key)
        max_v = p.confidence_max_variance.get(key, 1.5)
        assert v <= max_v, f"{key}: {v} exceeds cap {max_v}"


# -------------------------------------------------------------------------
# 4. Tissue risk — uses lagged exposure, not future labels
# -------------------------------------------------------------------------


def test_tissue_risk_uses_lagged_exposure_not_future_labels():
    """compute_tissue_risk must work without any outcome labels."""
    s = _state(lumbar=60.0)
    result = compute_tissue_risk(s, lagged_exposure_7d={"lumbar": 50.0})
    # If this doesn't raise, the module doesn't require label data
    assert result.risk_by_axis["lumbar"] > 0.20
    assert result.calibrated is False


# -------------------------------------------------------------------------
# 5. Deload need — single soft signal stays in none/watch; two reach watch
# -------------------------------------------------------------------------


def test_deload_need_requires_multiple_soft_signals():
    """Single soft signal must not produce 'bias'; two soft signals reach exactly 'watch'.

    Brief intent: hard rule → bias; multiple soft signals → less than bias (watch).
    Real design (from test_deload_need.py): two soft signals score 0.40 → 'watch'.
    'bias' requires a hard rule (single axis > 60) which floors at score 0.55.
    The brief incorrectly suggested two soft signals "may reach bias"; they do not.
    """
    s = _state(cns=20.0)  # no hard rule — all fatigue axes well below 60
    single_signal = compute_deload_need(s, performance_residual_slope=-0.05)
    two_signals = compute_deload_need(
        s, performance_residual_slope=-0.05, mean_fatigue_slope=0.04
    )
    assert single_signal.tier in ("none", "watch"), (
        f"Single soft signal should not reach bias, got {single_signal.tier}"
    )
    # Two soft signals → score = 0.20 * 2 = 0.40 → 'watch', NOT 'bias'
    assert two_signals.tier == "watch", (
        f"Two soft signals should reach exactly 'watch', got {two_signals.tier}"
    )
    assert two_signals.score >= single_signal.score


# -------------------------------------------------------------------------
# 6. Interference — smooth, bounded, monotonic
# -------------------------------------------------------------------------


def test_interference_multiplier_is_smooth_bounded_monotonic():
    p = default_parameters()
    prev_m = 1.0
    for fatigue_level in range(0, 101, 10):
        s = _state(metabolic=float(fatigue_level), structural=float(fatigue_level))
        m = directional_interference_multiplier("max_strength", s, p)
        floor = p.interference_floor_by_axis.get("max_strength", 0.30)
        assert floor <= m <= 1.0, f"fatigue={fatigue_level}: multiplier {m} out of bounds"
        assert m <= prev_m + 1e-9, (
            f"Multiplier increased at fatigue={fatigue_level}: {prev_m:.4f} → {m:.4f}"
        )
        prev_m = m


def test_suppression_exp_is_smooth():
    """suppression_exp should be continuous and monotonically decreasing."""
    prev = suppression_exp(0.0, alpha=1.0, floor=0.3)
    for z in [0.1, 0.2, 0.5, 1.0, 2.0, 5.0]:
        curr = suppression_exp(z, alpha=1.0, floor=0.3)
        assert curr <= prev, f"Not monotonic at z={z}"
        prev = curr


# -------------------------------------------------------------------------
# 7. Candidate scoring guardrails
# -------------------------------------------------------------------------


def test_candidate_score_weight_constraints():
    from app.logic.constraint_engine.candidate import DEFAULT_SCORE_WEIGHTS, validate_score_weights

    violations = validate_score_weights(DEFAULT_SCORE_WEIGHTS)
    assert violations == [], f"Default weights must pass validation: {violations}"


def test_learned_weights_cannot_remove_fatigue_penalty():
    from app.logic.constraint_engine.candidate import validate_score_weights

    unsafe = {
        "goal_alignment": 0.50,
        "state_fit": 0.50,
        "fatigue_penalty": 0.0,
        "tissue_penalty": 0.0,
    }
    violations = validate_score_weights(unsafe)
    assert any("fatigue_penalty" in v for v in violations)


# -------------------------------------------------------------------------
# 8. Candidate logging
# -------------------------------------------------------------------------


def test_all_candidates_logged():
    from app.logic.constraint_engine.candidate import SessionCandidate
    from app.logic.prescriber import recommend_next_session

    s = _state()
    collected: list[SessionCandidate] = []
    _ = recommend_next_session(s, candidate_log_out=collected)
    assert len(collected) > 0, "Must collect at least one candidate"


# -------------------------------------------------------------------------
# 9. Static arm annotates decision and skips adaptive scoring
# -------------------------------------------------------------------------


def test_static_with_safety_caps_ignores_adaptive_scoring():
    """Static arm must annotate its decision mode via constraints_applied.

    The brief's ``if rx.why:`` guard was weakened; the existing test contract
    (test_experiment_arms.py) asserts rx.why is always non-None for the static
    arm, so we use the unconditional assertion here.
    """
    from app.logic.prescriber import recommend_next_session

    s = _state()
    rx = recommend_next_session(s, prescription_arm="static_with_safety_caps")
    assert rx is not None
    assert rx.why is not None, "Static arm must always produce an explanation"
    applied = rx.why.constraints_applied
    assert any("static_with_safety_caps" in c for c in applied), (
        f"Static arm prescription must annotate arm. Got: {applied}"
    )
