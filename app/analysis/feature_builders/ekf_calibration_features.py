"""Production feed for the EKF calibration gate (ADR-0041).

Reads ``ekf_shadow_log`` update rows into ``EkfUpdateRecord``s carrying the stored
``nis``/``n_obs`` — enough for the NIS χ² consistency check on real athlete data. Interval
coverage needs the full predictive std and is produced by the replay harness instead.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.q10_confidence.ekf_calibration import EkfUpdateRecord
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
