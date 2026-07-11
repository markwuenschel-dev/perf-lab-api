"""Per-axis seed uncertainty by evidence tier (ADR-0059, seed_variance_policy_v1).

Retires the uniform ``SEED_CAPACITY_VARIANCE``: a squat-1RM-backed ``max_strength``
must not carry the same seed uncertainty as an unmeasured ``skill``. Seed variance is
``axis_base_variance[axis] × tier_multiplier[tier]`` over evidence tiers with a hard,
per-axis strict ordering — any validated measurement is more certain than an onramp
estimate, which beats a cross-axis inference, which beats an experience prior, which
beats an unseeded placeholder.

`source_type`, `evidence_tier`, and `variance` are **three separate facts** (the same
source class varies in quality by protocol/semantics). The values here are
**synthetic/expert priors** (``calibration_basis = synthetic_and_expert_prior``), not
empirically calibrated — that needs seed→retest data separating seed error from
adaptation, which needs elapsed time + training exposure.

One runtime authority: these values seed the LIVE per-axis ``CapacityConfidence`` (the
variance itself). The seed *snapshot* (app.logic.seed_snapshot) is immutable provenance
— never read at runtime for current provisionality.
"""

from __future__ import annotations

from app.domain.vectors import CapacityState

POLICY_VERSION = "seed_variance_policy_v1"
CALIBRATION_BASIS = "synthetic_and_expert_prior"

CAP_AXES: tuple[str, ...] = CapacityState.KEYS

# --- Evidence tiers, strictly ordered from most to least certain -------------
TIER_R_VALIDATED_BENCHMARK = "R_validated_benchmark"
TIER_DIRECT_MEASURED_ONRAMP = "direct_measured_onramp"
TIER_DIRECT_ESTIMATED_ONRAMP = "direct_estimated_onramp"
TIER_CROSS_AXIS_INFERENCE = "cross_axis_inference"
TIER_EXPERIENCE_PRIOR = "experience_prior"
TIER_UNSEEDED = "unseeded"

# The hard ordering enforced in code (weakest certainty last).
SEED_TIER_ORDER: tuple[str, ...] = (
    TIER_R_VALIDATED_BENCHMARK,
    TIER_DIRECT_MEASURED_ONRAMP,
    TIER_DIRECT_ESTIMATED_ONRAMP,
    TIER_CROSS_AXIS_INFERENCE,
    TIER_EXPERIENCE_PRIOR,
    TIER_UNSEEDED,
)

# Strictly increasing: more uncertainty for weaker evidence. Calibrated on the engine's
# compressed relative variance scale (weak prior ≈ 1.0, cap = 1.5), NOT the absolute
# 100²/12 scale — the production confidence path is normalized (ADR-0034/0036).
TIER_MULTIPLIER: dict[str, float] = {
    TIER_R_VALIDATED_BENCHMARK: 0.12,
    TIER_DIRECT_MEASURED_ONRAMP: 0.30,
    TIER_DIRECT_ESTIMATED_ONRAMP: 0.55,
    TIER_CROSS_AXIS_INFERENCE: 0.80,
    TIER_EXPERIENCE_PRIOR: 1.0,
    TIER_UNSEEDED: 1.5,
}

# Axis-scaled base: inherently more measurable axes (1RM, 5K) carry a lower base than
# fuzzy ones (skill, mobility). Centered near the legacy uniform 1.0 so the common
# experience-prior baseline stays close to prior behavior.
AXIS_BASE_VARIANCE: dict[str, float] = {
    "max_strength": 0.85,
    "aerobic": 0.85,
    "power": 0.95,
    "glycolytic": 0.95,
    "work_capacity": 0.95,
    "hypertrophy": 1.0,
    "skill": 1.0,
    "mobility": 1.0,
}

# Aligns with EngineParameters.confidence_max_variance (1.5). An unseeded neutral value
# (skill/mobility = 50) is a bounded, capped placeholder — never displayed as known,
# never an observation — not an unbounded prior.
MAX_SEED_VARIANCE = 1.5

# Provenance (evidence_status) that each tier maps to — distinct from the variance.
TIER_TO_EVIDENCE_STATUS: dict[str, str] = {
    TIER_R_VALIDATED_BENCHMARK: "measured",
    TIER_DIRECT_MEASURED_ONRAMP: "measured",
    TIER_DIRECT_ESTIMATED_ONRAMP: "estimated",
    TIER_CROSS_AXIS_INFERENCE: "inferred",
    TIER_EXPERIENCE_PRIOR: "experience_prior",
    TIER_UNSEEDED: "unobserved",
}


def seed_variance(axis: str, tier: str) -> float:
    """Per-axis seed variance for an evidence tier, capped at ``MAX_SEED_VARIANCE``."""
    if axis not in AXIS_BASE_VARIANCE:
        raise ValueError(f"unknown capacity axis: {axis!r}")
    if tier not in TIER_MULTIPLIER:
        raise ValueError(f"unknown evidence tier: {tier!r}")
    return min(MAX_SEED_VARIANCE, AXIS_BASE_VARIANCE[axis] * TIER_MULTIPLIER[tier])


def evidence_status_for_tier(tier: str) -> str:
    """Provenance label for a tier (measured / estimated / inferred / …)."""
    return TIER_TO_EVIDENCE_STATUS[tier]


# --- Invariants (checked at import; re-asserted in tests) --------------------
# Tier multipliers strictly increase along the order.
assert all(
    TIER_MULTIPLIER[SEED_TIER_ORDER[i]] < TIER_MULTIPLIER[SEED_TIER_ORDER[i + 1]]
    for i in range(len(SEED_TIER_ORDER) - 1)
), "TIER_MULTIPLIER must strictly increase along SEED_TIER_ORDER"
# Per-axis strict ordering: for every axis, variance strictly increases across tiers
# (the cap must not flatten two adjacent tiers).
assert all(
    seed_variance(axis, SEED_TIER_ORDER[i]) < seed_variance(axis, SEED_TIER_ORDER[i + 1])
    for axis in CAP_AXES
    for i in range(len(SEED_TIER_ORDER) - 1)
), "seed_variance must strictly increase per axis across SEED_TIER_ORDER"


# --- Baseline tier plan ------------------------------------------------------

def baseline_tier_plan(
    *,
    has_strength_input: bool = False,
    has_run_input: bool = False,
) -> dict[str, tuple[str, str]]:
    """Per-axis ``(evidence_tier, source)`` for an onboarding baseline seed.

    A plain experience-level baseline seeds capacity axes as ``experience_prior`` and
    the two fuzzy neutral axes (skill/mobility = 50) as ``unseeded`` placeholders. A
    self-reported 1RM / 5K promotes that axis to ``direct_estimated_onramp`` and
    cross-infers ``power`` from strength (retaining that it is inference, not a prior).
    """
    plan: dict[str, tuple[str, str]] = dict.fromkeys(CAP_AXES, (TIER_EXPERIENCE_PRIOR, "experience_prior"))
    plan["skill"] = (TIER_UNSEEDED, "none")
    plan["mobility"] = (TIER_UNSEEDED, "none")
    if has_strength_input:
        plan["max_strength"] = (TIER_DIRECT_ESTIMATED_ONRAMP, "self_report_1rm")
        plan["power"] = (TIER_CROSS_AXIS_INFERENCE, "cross_axis:max_strength")
    if has_run_input:
        plan["aerobic"] = (TIER_DIRECT_ESTIMATED_ONRAMP, "self_report_5k")
    return plan


def seed_confidence_overrides(plan: dict[str, tuple[str, str]]) -> dict[str, float]:
    """Per-axis LIVE ``CapacityConfidence`` variance derived from a tier plan."""
    return {axis: seed_variance(axis, tier) for axis, (tier, _src) in plan.items()}
