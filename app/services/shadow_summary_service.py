"""Read-only inspection of an athlete's shadow subsystems (ADR-0041/0042/0043).

Aggregates the capture-only shadow logs into one summary so the shadow work is actually
observable: the EKF belief + calibration, the MPC planner's agreement with the greedy
prescriber, dose model v1 beside v0 (phase 8A), per-athlete recovery personalization, and
the recovery-clearance shadow. Purely
read-only — it never triggers a computation or changes state; each section is null/empty when
that subsystem has no rows yet for the athlete.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.dose_fit_policy import fit_tier
from app.models.dose_model_shadow import DoseModelShadowLog
from app.models.mpc_shadow import MpcShadowLog
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog

_MPC_WINDOW = 30
_DOSE_WINDOW = 30


async def _ekf_section(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    # Lazy import: ``ekf_calibration_features`` transitively pulls in the offline ML
    # stack (``app.ml.q10_confidence`` → pandas), which is NOT installed in the lean
    # production image. Importing it at module load crashed app startup (uvicorn could
    # not import app.main → the container stopped → /ping healthcheck failed, breaking
    # every deploy since 2026-07-07). Deferring it here keeps the whole app bootable;
    # only this one read-only shadow-summary section needs the heavy dep.
    from app.analysis.feature_builders.ekf_calibration_features import summarize_ekf_shadow

    s = await summarize_ekf_shadow(db, user_id)
    if s["n_predict"] == 0 and s["n_update"] == 0:
        return None
    latest_trace = s["trace_series"][-1]["trace"] if s["trace_series"] else None
    cal = s["calibration"]
    return {
        "n_predict": s["n_predict"],
        "n_update": s["n_update"],
        "latest_trace": latest_trace,
        "nis": cal.get("nis", {}),
        "calibration_verdict": cal.get("verdict"),
    }


async def _mpc_section(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    rows = list((await db.execute(
        select(MpcShadowLog).where(MpcShadowLog.user_id == user_id)
        .order_by(MpcShadowLog.id.desc()).limit(_MPC_WINDOW)
    )).scalars().all())
    if not rows:
        return None
    agree = sum(1 for r in rows if r.agreement) / len(rows)
    latest = rows[0]
    return {
        "n_decisions": len(rows),
        "agreement_rate": round(agree, 3),
        "latest": {
            "greedy_type": latest.greedy_type,
            "mpc_type": latest.mpc_type,
            "agreement": latest.agreement,
            "belief_trace": latest.belief_trace,
        },
    }


async def _dose_model_section(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    """Dose model v1 beside v0 (8A): recent rows, by version, and what a fit could use.

    Ratios are summarized for the LATEST v1 version only: a v1.1 ratio and a v1.2 ratio are
    different models' answers and are never pooled.
    """
    rows = list((await db.execute(
        select(DoseModelShadowLog).where(DoseModelShadowLog.user_id == user_id)
        .order_by(DoseModelShadowLog.id.desc()).limit(_DOSE_WINDOW)
    )).scalars().all())
    if not rows:
        return None
    latest = rows[0]
    by_version: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for r in rows:
        by_version[r.v1_model_version] = by_version.get(r.v1_model_version, 0) + 1
        tier = fit_tier(
            v0_volume_used_fabricated_sets=r.v0_volume_used_fabricated_sets,
            v1_density_basis=r.v1_density_basis,
        )
        tiers[tier] = tiers.get(tier, 0) + 1
    ratios = [
        r.ratio_v1_v0 for r in rows
        if r.v1_model_version == latest.v1_model_version and r.ratio_v1_v0 is not None
    ]
    return {
        "n_rows": len(rows),
        "rows_by_v1_model_version": dict(sorted(by_version.items())),
        "rows_by_fit_tier": dict(sorted(tiers.items())),
        "latest_version_ratio": (
            {"n": len(ratios), "median": round(median(ratios), 4),
             "min": round(min(ratios), 4), "max": round(max(ratios), 4)}
            if ratios else None
        ),
        "latest": {
            "v1_model_version": latest.v1_model_version,
            "v1_density_basis": latest.v1_density_basis,
            "ratio_v1_v0": latest.ratio_v1_v0,
            "workload_preference": latest.workload_preference,
            "prescription_branch": latest.prescription_branch,
        },
        "decision_impact": "none_shadow_only",
    }


async def _personalization_section(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    row = (await db.execute(
        select(PersonalizationShadowLog).where(PersonalizationShadowLog.user_id == user_id)
        .order_by(PersonalizationShadowLog.id.desc()).limit(1)
    )).scalars().first()
    if row is None:
        return None
    delta = {
        axis: round(float(row.personalized_multiplier.get(axis, 0.0)) - float(row.population_multiplier.get(axis, 0.0)), 4)
        for axis in row.population_multiplier
    }
    return {
        "parameter": row.parameter,
        "active": row.shrinkage_weight > 0.0,
        "n_obs": row.n_obs,
        "shrinkage_weight": row.shrinkage_weight,
        "theta_trace": row.theta_trace,
        "multiplier_delta": delta,
    }


async def _recovery_section(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    row = (await db.execute(
        select(RecoveryShadowLog).where(RecoveryShadowLog.user_id == user_id)
        .order_by(RecoveryShadowLog.id.desc()).limit(1)
    )).scalars().first()
    if row is None:
        return None
    return {
        "model_version": row.model_version,
        "baseline_clearance_multiplier": row.baseline_clearance_multiplier,
        "learned_clearance_multiplier": row.learned_clearance_multiplier,
    }


async def athlete_shadow_summary(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """One read-only view of every shadow subsystem's latest state for an athlete."""
    return {
        "user_id": user_id,
        "ekf": await _ekf_section(db, user_id),
        "mpc": await _mpc_section(db, user_id),
        "dose_model": await _dose_model_section(db, user_id),
        "personalization": await _personalization_section(db, user_id),
        "recovery": await _recovery_section(db, user_id),
    }
