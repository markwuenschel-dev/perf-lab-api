"""Immutable per-axis seed provenance snapshot (ADR-0059).

The seed snapshot records **how each capacity axis was seeded** at onboarding —
source, evidence tier, and the seed variance that was written to the live
``CapacityConfidence`` at that instant. It is immutable provenance for audit /
analytics / debt-explanation, and is **never read at runtime for current
provisionality** — the live per-axis ``CapacityConfidence`` variance is the single
runtime authority (a static check, tests/test_seed_snapshot_not_runtime_read.py,
forbids runtime engine modules from importing this module).

The P7 scalar ``initial_seed_status`` / ``initial_seed_confidence`` columns are
**derived analytics rollups** over this snapshot (explicitly versioned), not a parallel
confidence authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.logic import seed_variance as sv

SNAPSHOT_VERSION = sv.POLICY_VERSION
ROLLUP_VERSION = "initial_seed_status_rollup_v1"


def build_seed_snapshot(
    plan: dict[str, tuple[str, str]], *, seeded_at: datetime
) -> dict[str, Any]:
    """Assemble the immutable snapshot from a per-axis ``(tier, source)`` plan.

    Records ``source``, ``evidence_tier``, ``seed_variance`` and the provenance
    ``evidence_status`` per axis — three separate facts, no 1:1 collapse. A cross-axis
    inference additionally retains its ``seed_group`` lineage so no downstream service
    can count same-group axes as independent evidence (diagonal-covariance guardrail).
    """
    by_axis: dict[str, Any] = {}
    for axis, (tier, source) in plan.items():
        entry: dict[str, Any] = {
            "source": source,
            "evidence_tier": tier,
            "seed_variance": sv.seed_variance(axis, tier),
            "evidence_status": sv.evidence_status_for_tier(tier),
        }
        if tier == sv.TIER_CROSS_AXIS_INFERENCE:
            # lineage: same seed_group axes are correlated, not independent evidence.
            entry["seed_group"] = source  # e.g. "cross_axis:max_strength"
        by_axis[axis] = entry
    return {
        "policy_version": SNAPSHOT_VERSION,
        "calibration_basis": sv.CALIBRATION_BASIS,
        "seeded_at": seeded_at.isoformat(),
        "by_axis": by_axis,
    }


def initial_seed_status_rollup(snapshot: dict[str, Any] | None) -> str:
    """Versioned scalar rollup over the snapshot (analytics only, not authority).

    ``none`` (no snapshot) | ``experience_prior_only`` (nothing measured/estimated) |
    ``benchmark_seeded`` (all seeded axes are measured/estimated) | ``mixed``.
    """
    if not snapshot:
        return "none"
    by_axis: dict[str, Any] = snapshot.get("by_axis") or {}
    statuses: set[str] = {str(e.get("evidence_status")) for e in by_axis.values()}
    measured_like = statuses & {"measured", "estimated"}
    weak_like = statuses & {"experience_prior", "inferred", "unobserved"}
    if measured_like and weak_like:
        return "mixed"
    if measured_like:
        return "benchmark_seeded"
    return "experience_prior_only"
