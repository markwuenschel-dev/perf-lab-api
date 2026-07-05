"""Production feed + inspection for the EKF calibration gate (ADR-0041).

Reads ``ekf_shadow_log`` rows into ``EkfUpdateRecord``s carrying the stored ``nis``/``n_obs``
— enough for the NIS χ² consistency check on real athlete data — and summarizes an
athlete's belief stream so the shadow estimator is actually inspectable. Interval coverage
needs the full predictive std and is produced by the replay harness instead.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.q10_confidence.ekf_calibration import EkfUpdateRecord, calibration_report
from app.models.ekf_shadow import EkfShadowLog


async def build_ekf_calibration_records(
    db: AsyncSession,
    user_id: int | None = None,
) -> list[EkfUpdateRecord]:
    """Load EKF update rows (optionally for one athlete) as NIS calibration records."""
    stmt = select(EkfShadowLog).where(EkfShadowLog.event_type == "update")
    if user_id is not None:
        stmt = stmt.where(EkfShadowLog.user_id == user_id)
    stmt = stmt.order_by(EkfShadowLog.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        EkfUpdateRecord(nis=float(r.nis), n_obs=int(r.n_obs))
        for r in rows
        if r.nis is not None and r.n_obs
    ]


async def summarize_ekf_shadow(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """Summarize one athlete's shadow belief stream for inspection.

    Returns the total-uncertainty ``trace`` series over time (both predict and update
    rows), the per-update ``nis`` series, and a NIS-based ``calibration`` report. Interval
    coverage is ``nan`` here — it needs the predictive std, which only the replay harness
    has; NIS is the production consistency check, as designed.
    """
    stmt = (
        select(EkfShadowLog)
        .where(EkfShadowLog.user_id == user_id)
        .order_by(EkfShadowLog.id)
    )
    rows = list((await db.execute(stmt)).scalars().all())

    trace_series = [
        {
            "belief_at": r.belief_at.isoformat(),
            "event_type": r.event_type,
            "trace": float(sum(r.variance_json.values())) if r.variance_json else 0.0,
        }
        for r in rows
    ]
    nis_series = [
        {
            "belief_at": r.belief_at.isoformat(),
            "benchmark_code": r.benchmark_code,
            "nis": float(r.nis),
            "n_obs": int(r.n_obs),
        }
        for r in rows
        if r.event_type == "update" and r.nis is not None and r.n_obs
    ]
    records = [EkfUpdateRecord(nis=n["nis"], n_obs=n["n_obs"]) for n in nis_series]

    return {
        "user_id": user_id,
        "n_predict": sum(1 for r in rows if r.event_type == "predict"),
        "n_update": len(nis_series),
        "trace_series": trace_series,
        "nis_series": nis_series,
        "calibration": asdict(calibration_report(records)),
    }
