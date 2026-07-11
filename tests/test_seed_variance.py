"""ADR-0059 — per-axis seed uncertainty by evidence tier + provenance views (pure)."""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.vectors import CapacityState
from app.logic import confidence_presentation as cp
from app.logic import seed_snapshot as ss
from app.logic import seed_variance as sv

# ── seed variance policy ──────────────────────────────────────────────────────

def test_tier_multiplier_strictly_increases() -> None:
    mults = [sv.TIER_MULTIPLIER[t] for t in sv.SEED_TIER_ORDER]
    assert mults == sorted(mults)
    assert len(set(mults)) == len(mults)  # strictly, no ties


def test_seed_variance_strictly_increases_per_axis() -> None:
    for axis in CapacityState.KEYS:
        vals = [sv.seed_variance(axis, t) for t in sv.SEED_TIER_ORDER]
        assert vals == sorted(vals)
        assert len(set(vals)) == len(vals), (axis, vals)  # cap must not flatten a pair


def test_seed_variance_respects_cap_and_axis_scaling() -> None:
    for axis in CapacityState.KEYS:
        for tier in sv.SEED_TIER_ORDER:
            assert 0.0 < sv.seed_variance(axis, tier) <= sv.MAX_SEED_VARIANCE
    # a validated benchmark is far more certain than an unseeded placeholder
    assert sv.seed_variance("max_strength", sv.TIER_R_VALIDATED_BENCHMARK) < sv.seed_variance(
        "max_strength", sv.TIER_UNSEEDED
    )
    # inherently fuzzier axes carry more seed uncertainty at the same tier
    assert sv.seed_variance("skill", sv.TIER_EXPERIENCE_PRIOR) >= sv.seed_variance(
        "max_strength", sv.TIER_EXPERIENCE_PRIOR
    )


def test_seed_variance_rejects_unknown_axis_or_tier() -> None:
    import pytest

    with pytest.raises(ValueError):
        sv.seed_variance("vo2max", sv.TIER_EXPERIENCE_PRIOR)
    with pytest.raises(ValueError):
        sv.seed_variance("skill", "made_up_tier")


# ── baseline tier plan ────────────────────────────────────────────────────────

def test_plain_baseline_plan_is_experience_prior_with_unseeded_fuzzy_axes() -> None:
    plan = sv.baseline_tier_plan()
    assert plan["max_strength"][0] == sv.TIER_EXPERIENCE_PRIOR
    assert plan["skill"][0] == sv.TIER_UNSEEDED
    assert plan["mobility"][0] == sv.TIER_UNSEEDED


def test_self_reported_inputs_promote_and_cross_infer() -> None:
    plan = sv.baseline_tier_plan(has_strength_input=True, has_run_input=True)
    assert plan["max_strength"][0] == sv.TIER_DIRECT_ESTIMATED_ONRAMP
    assert plan["aerobic"][0] == sv.TIER_DIRECT_ESTIMATED_ONRAMP
    # power is inferred from strength — retains that it is inference, not a prior
    assert plan["power"][0] == sv.TIER_CROSS_AXIS_INFERENCE


# ── snapshot + rollup (provenance) ────────────────────────────────────────────

def test_snapshot_records_three_separate_facts_and_lineage() -> None:
    plan = sv.baseline_tier_plan(has_strength_input=True)
    snap = ss.build_seed_snapshot(plan, seeded_at=datetime.now(UTC))
    ms = snap["by_axis"]["max_strength"]
    assert ms["evidence_tier"] == sv.TIER_DIRECT_ESTIMATED_ONRAMP
    assert ms["evidence_status"] == "estimated"
    assert ms["seed_variance"] == sv.seed_variance("max_strength", sv.TIER_DIRECT_ESTIMATED_ONRAMP)
    # cross-axis inference retains seed_group lineage (not independent evidence)
    assert snap["by_axis"]["power"]["seed_group"] == "cross_axis:max_strength"
    assert snap["policy_version"] == sv.POLICY_VERSION


def test_initial_seed_status_rollup() -> None:
    assert ss.initial_seed_status_rollup(None) == "none"
    plain = ss.build_seed_snapshot(sv.baseline_tier_plan(), seeded_at=datetime.now(UTC))
    assert ss.initial_seed_status_rollup(plain) == "experience_prior_only"
    mixed = ss.build_seed_snapshot(
        sv.baseline_tier_plan(has_strength_input=True), seeded_at=datetime.now(UTC)
    )
    assert ss.initial_seed_status_rollup(mixed) == "mixed"


# ── confidence presentation (live variance only) ──────────────────────────────

def test_confidence_status_bands() -> None:
    assert cp.confidence_status(0.08) == cp.STATUS_ESTABLISHED     # a fresh measurement
    assert cp.confidence_status(1.0) == cp.STATUS_PROVISIONAL      # an experience prior
    assert cp.confidence_status(1.5) == cp.STATUS_INSUFFICIENT     # unseeded placeholder


def test_measured_but_provisional_is_expressible() -> None:
    # evidence_status (provenance) and confidence_status (live variance) are orthogonal:
    # a measured axis whose live variance has grown is "measured" yet "provisional".
    assert sv.evidence_status_for_tier(sv.TIER_R_VALIDATED_BENCHMARK) == "measured"
    assert cp.confidence_status(0.9) == cp.STATUS_PROVISIONAL
